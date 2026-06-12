"""
token_server.py — Local HTTP server that receives tokens from Tampermonkey
=========================================================================
Listens on localhost:5588 for POST /tokens with:
  { "firebase_token": "...", "cf_jwt": "..." }

Updates config.json and notifies the running WSTransport to hot-swap tokens.
"""

import asyncio
import json
import time
import logging
from aiohttp import web
from typing import Callable, Optional

logger = logging.getLogger("token_server")

TOKEN_PORT = 5588


class TokenServer:
    """
    Tiny HTTP server that receives fresh tokens from the Tampermonkey script.
    
    Usage:
        server = TokenServer(config_path="config.json")
        server.on_token_refresh = my_callback  # async (firebase, cf_jwt) -> None
        await server.start()
    """

    def __init__(self, config_path: str = "config.json", port: int = TOKEN_PORT):
        self.config_path = config_path
        self.port = port
        self.on_token_refresh: Optional[Callable] = None  # async callback
        self._runner: Optional[web.AppRunner] = None
        self.last_refresh: float = 0
        self.refresh_count: int = 0

    async def start(self):
        """Start the HTTP server in the background."""
        app = web.Application()
        app.router.add_post("/tokens", self._handle_tokens)
        app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        try:
            await site.start()
        except OSError as e:
            if e.errno == 98:  # Address already in use
                print(f"[TOKEN_SERVER] ℹ️ Port {self.port} is already in use. A background token server daemon is likely already running. Skipping local token server binding.")
                logger.info(f"Port {self.port} is already in use. Skipping local token server binding.")
                await self._runner.cleanup()
                self._runner = None
            else:
                raise e

    async def stop(self):
        """Shut down the server."""
        if self._runner:
            await self._runner.cleanup()

    async def _handle_tokens(self, request: web.Request) -> web.Response:
        """Handle POST /tokens from Tampermonkey."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        firebase_token = data.get("firebase_token") or ""
        cf_jwt = data.get("cf_jwt") or ""

        if not firebase_token or not cf_jwt:
            return web.json_response({"error": "missing tokens"}, status=400)

        self.refresh_count += 1
        self.last_refresh = time.time()

        logger.info(f"[TOKEN] ✅ Fresh tokens received (#{self.refresh_count})")
        logger.info(f"[TOKEN] firebase_token: ...{firebase_token[-20:]}")
        logger.info(f"[TOKEN] cf_jwt: ...{cf_jwt[-20:]}")

        # Update config.json
        self._update_config(firebase_token, cf_jwt)

        # Notify the client
        if self.on_token_refresh:
            try:
                await self.on_token_refresh(firebase_token, cf_jwt)
            except Exception as e:
                logger.error(f"[TOKEN] Callback error: {e}")

        return web.json_response({
            "status": "ok",
            "refresh_count": self.refresh_count,
        })

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "running",
            "refresh_count": self.refresh_count,
            "last_refresh": self.last_refresh,
        })

    def _update_config(self, firebase_token: str, cf_jwt: str):
        """Write fresh tokens to config.json."""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["WOLVESVILLE_TOKEN"] = firebase_token
            cfg["WOLVESVILLE_CF_JWT"] = cf_jwt
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4)
            logger.info("[TOKEN] config.json updated")
        except Exception as e:
            logger.error(f"[TOKEN] Failed to update config: {e}")


if __name__ == "__main__":
    import sys
    # Setup basic logging to console
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    print("\n" + "=" * 60)
    print("                WOLVESVILLE TOKEN SERVER                    ".center(60))
    print("=" * 60)
    print(" Listening on : http://127.0.0.1:5588/tokens")
    print(" Target Config: config.json")
    print(" Status       : Waiting for tokens from Tampermonkey...")
    print("=" * 60 + "\n")
    
    server = TokenServer(config_path="config.json")
    
    async def run_server():
        await server.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await server.stop()
            print("\nToken Server gracefully stopped.")

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("\nExiting...")
