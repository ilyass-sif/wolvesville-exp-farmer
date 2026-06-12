"""
ws_transport.py — Direct WebSocket Transport (Engine.IO v4 / Socket.IO)
=======================================================================
Replaces the Tampermonkey HTTP bridge by connecting directly to:
  wss://game.api-wolvesville.com/socket.io/

Engine.IO v4 wire protocol:
  Recv  0{...}    → OPEN   (contains sid, pingInterval, pingTimeout)
  Recv  2         → PING   (must reply with 3 = PONG within pingTimeout)
  Send  3         → PONG
  Send  40        → Socket.IO CONNECT (sent after EIO OPEN)
  Recv  40{...}   → Socket.IO CONNECT ACK
  Both  42[...]   → Socket.IO EVENT (game messages)

Connecting with gameMode=en and dodgeBlockedPlayers=true
automatically joins a quick game on the server side.
"""

import asyncio
import json
import time
import base64
import logging
from typing import Callable, Optional
import websockets
from logger import BotLogger
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger("ws_transport")


def _decode_jwt_exp(token: str) -> Optional[int]:
    """Decode the 'exp' field from a JWT without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        padding = 4 - len(parts[1]) % 4
        payload_b64 = parts[1] + ("=" * padding)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("exp")
    except Exception:
        return None


def _decode_jwt_user_agent(token: str) -> Optional[str]:
    """Decode the 'user-agent' or 'userAgent' field from a JWT without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        padding = 4 - len(parts[1]) % 4
        payload_b64 = parts[1] + ("=" * padding)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("user-agent") or payload.get("userAgent")
    except Exception:
        return None


def check_token_expiry(firebase_token: str, cf_jwt: str) -> bool:
    """
    Check if tokens are still valid. Prints warnings if near expiry.
    Returns True if tokens appear valid, False if already expired.
    """
    now = int(time.time())
    valid = True

    for name, token in [("firebaseToken", firebase_token), ("Cf-JWT", cf_jwt)]:
        exp = _decode_jwt_exp(token)
        if exp is None:
            logger.warning(f"[WS] Could not decode expiry from {name}")
            continue
        remaining = exp - now
        if remaining <= 0:
            logger.error(f"[WS] ❌ {name} has EXPIRED {abs(remaining)//60}m ago! Update config.json.")
            valid = False
        elif remaining < 300:
            logger.warning(f"[WS] ⚠️  {name} expires in {remaining}s — update soon!")
        else:
            logger.info(f"[WS] ✅ {name} valid for {remaining//60}m {remaining%60}s")

    return valid


def build_ws_url(
    firebase_token: str,
    cf_jwt: str,
    game_mode: str = "en",
    game_id: Optional[str] = None,
    spectate: bool = False,
    random_avatar_slot: bool = False,
    dodge_blocked: bool = True,
    device_id: str = "null",
    ids: int = 1,
    api_v: int = 1,
    build_version: int = 79,
) -> str:
    """Build the full WSS URL for joining a game."""
    params = (
        f"firebaseToken={firebase_token}"
    )
    if game_id:
        params += f"&gameId={game_id}"
        
    params += f"&gameMode={game_mode}"
    
    if game_mode == "custom":
        params += "&password=undefined"
    else:
        params += (
            f"&spectate={'true' if spectate else 'false'}"
            f"&randomAvatarSlot={'true' if random_avatar_slot else 'false'}"
            f"&dodgeBlockedPlayers={'true' if dodge_blocked else 'false'}"
        )
        
    params += (
        f"&deviceId={device_id}"
        f"&ids={ids}"
        f"&Cf-JWT={cf_jwt}"
    )

    if game_mode != "custom":
        params += (
            f"&apiV={api_v}"
            f"&b={build_version}"
        )

    params += (
        f"&EIO=4"
        f"&transport=websocket"
    )
    return f"wss://game.api-wolvesville.com/socket.io/?{params}"


class WSTransport:
    """
    Async WebSocket transport for Wolvesville game server.
    Handles the Engine.IO v4 + Socket.IO framing layer.
    """

    BASE_URL = "wss://game.api-wolvesville.com/socket.io/"

    def __init__(
        self,
        firebase_token: str,
        cf_jwt: str,
        game_mode: str = "en",
        game_id: Optional[str] = None,
        build_version: int = 79,
        reconnect: bool = True,
        max_reconnect_attempts: int = 5,
    ):
        self.firebase_token = firebase_token
        self.cf_jwt = cf_jwt
        self.game_mode = game_mode
        self.game_id = game_id
        self.build_version = build_version
        self.reconnect = reconnect
        self.max_reconnect_attempts = max_reconnect_attempts

        self._ws: Optional[websockets.ClientConnection] = None
        self._ping_interval: float = 25.0
        self._ping_timeout: float = 20.0
        self._last_ping: float = 0.0
        self._sid: Optional[str] = None

        self.is_connected: bool = False
        self._connect_attempt: int = 0
        self._running: bool = False
        self._ping_task: Optional[asyncio.Task] = None

        # Callbacks set by WolvesvilleClient
        self.on_message: Optional[Callable] = None   # async (raw_str) -> None
        self.on_connect: Optional[Callable] = None   # async () -> None
        self.on_disconnect: Optional[Callable] = None  # async () -> None

    def _build_url(self) -> str:
        return build_ws_url(
            self.firebase_token,
            self.cf_jwt,
            game_mode=self.game_mode,
            game_id=self.game_id,
            build_version=self.build_version,
        )

    async def connect(self):
        """Start the transport. Connects and maintains the WebSocket."""
        self._running = True
        self._connect_attempt = 0

        while self._running:
            ws = None
            try:
                url = self._build_url()
                logger.info(f"[WS] Connecting to game server (attempt {self._connect_attempt + 1})...")
                BotLogger.client(f"Connecting to wss://game.api-wolvesville.com/ ...")

                # Dynamically extract User-Agent from the Cloudflare JWT token
                ua = _decode_jwt_user_agent(self.cf_jwt) or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
                logger.info(f"[WS] Using dynamic User-Agent extracted from JWT: {ua[:60]}...")

                extra_headers = {
                    "User-Agent": ua,
                    "Origin": "https://wolvesville.com",
                }

                # websockets v13+ API: await connect() directly (no async with)
                ws = await websockets.connect(
                    url,
                    additional_headers=extra_headers,
                    ping_interval=None,   # We handle pings manually (EIO protocol)
                    ping_timeout=None,
                    max_size=10 * 1024 * 1024,
                    open_timeout=15,
                )
                self._ws = ws
                self._connect_attempt = 0
                BotLogger.client(f"✅ WebSocket connected!")

                # Start ping timeout watchdog (detects dead connections)
                self._ping_task = asyncio.create_task(self._ping_watchdog())

                try:
                    await self._receive_loop()
                finally:
                    if self._ping_task:
                        self._ping_task.cancel()
                        try:
                            await self._ping_task
                        except asyncio.CancelledError:
                            pass
                    self._ping_task = None

            except ConnectionClosed as e:
                logger.info(f"[WS] Connection closed: {e}")
            except Exception as e:
                logger.error(f"[WS] Connection error: {e}")
            finally:
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                self._ws = None
                self.is_connected = False
                if self.on_disconnect:
                    try:
                        await self.on_disconnect()
                    except Exception as ex:
                        logger.error(f"[WS] on_disconnect error: {ex}")

            if not self._running:
                break

            if not self.reconnect:
                logger.info("[WS] Reconnect disabled, stopping.")
                break

            self._connect_attempt += 1
            if self._connect_attempt > self.max_reconnect_attempts:
                logger.error(f"[WS] ❌ Max reconnect attempts reached ({self.max_reconnect_attempts}). Stopping.")
                break

            backoff = min(2 ** self._connect_attempt, 60)
            logger.info(f"[WS] Reconnecting in {backoff}s (attempt {self._connect_attempt}/{self.max_reconnect_attempts})...")
            await asyncio.sleep(backoff)

    async def _receive_loop(self):
        """Main receive loop — handles Engine.IO framing."""
        async for raw in self._ws:
            if not isinstance(raw, str):
                continue  # Ignore binary frames
            await self._handle_eio_frame(raw)

    async def _handle_eio_frame(self, raw: str):
        """Handle a single Engine.IO v4 frame."""
        if not raw:
            return

        eio_type = raw[0]

        if eio_type == "0":
            # EIO OPEN — parse server handshake
            try:
                data = json.loads(raw[1:])
                self._sid = data.get("sid")
                self._ping_interval = data.get("pingInterval", 25000) / 1000
                self._ping_timeout = data.get("pingTimeout", 20000) / 1000
                logger.info(f"[WS] EIO OPEN: sid={self._sid}, ping_interval={self._ping_interval}s")
                # BotLogger.client removed to silence console
            except Exception as e:
                logger.error(f"[WS] Failed to parse EIO OPEN: {e}")
            # Send Socket.IO CONNECT
            await self._raw_send("40")

        elif eio_type == "2":
            # EIO PING from server — reply with PONG
            self._last_ping = time.time()
            await self._raw_send("3")
            logger.debug("[WS] PING → PONG")

        elif eio_type == "4":
            # Socket.IO packet
            sio_type = raw[1] if len(raw) > 1 else ""

            if sio_type == "0":
                # Socket.IO CONNECT ACK
                self.is_connected = True
                # BotLogger.client removed to silence console
                if self.on_connect:
                    try:
                        await self.on_connect()
                    except Exception as e:
                        logger.error(f"[WS] on_connect error: {e}")

            elif sio_type == "2":
                # Socket.IO EVENT (42[...]) — forward to client
                if self.on_message:
                    try:
                        await self.on_message(raw)
                    except Exception as e:
                        logger.error(f"[WS] on_message error: {e}")

            elif sio_type == "4":
                # Socket.IO ERROR
                logger.error(f"[WS] Socket.IO error: {raw}")

        else:
            logger.debug(f"[WS] Unhandled EIO frame type '{eio_type}': {raw[:80]}")

    async def _ping_watchdog(self):
        """
        Watchdog: the server sends EIO PING every ~25s. If we don't
        receive one within (pingInterval + pingTimeout) the connection
        is dead. We NEVER send our own PINGs — the real browser client
        doesn't either. We only reply PONG (raw '3') to server PINGs,
        which is handled in _handle_eio_frame.
        """
        deadline = self._ping_interval + self._ping_timeout  # ~45s
        try:
            while True:
                await asyncio.sleep(deadline)
                elapsed = time.time() - self._last_ping if self._last_ping else 999
                if elapsed > deadline:
                    logger.warning(f"[WS] No server PING for {elapsed:.0f}s — connection likely dead")
                    if self._ws:
                        try:
                            await self._ws.close()
                        except Exception:
                            pass
                    break
        except asyncio.CancelledError:
            pass

    async def _raw_send(self, data: str):
        """Send a raw frame over the WebSocket."""
        if self._ws is not None:
            await self._ws.send(data)

    async def send(self, raw_socketio: str):
        """
        Send a Socket.IO event frame (e.g. 42["event", payload]).
        This is what WolvesvilleClient._emit() calls.
        """
        if not self.is_connected or not self._ws:
            logger.debug(f"[WS] Not connected — cannot send: {raw_socketio[:60]}")
            return False
        try:
            await self._ws.send(raw_socketio)
            return True
        except Exception as e:
            logger.error(f"[WS] Send error: {e}")
            return False

    async def disconnect(self):
        """Gracefully close the WebSocket connection."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("[WS] Disconnected.")

    async def reconnect_for_new_game(self):
        """
        Leave current game and join a new one by closing and reopening
        the WebSocket. The server automatically puts you in matchmaking
        on each new connection.
        """
        BotLogger.info("Reconnecting for new game...")
        self._running = True  # Keep reconnect loop alive
        if self._ws:
            try:
                await self._ws.close(code=1000, reason="leaving")
            except Exception:
                pass
        # The main connect() loop will handle reconnecting automatically
