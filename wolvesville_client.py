"""
wolvesville_client.py — Bidirectional Wolvesville Client (Direct WebSocket)
===========================================================================
Connects directly to wss://game.api-wolvesville.com/socket.io/ using a
firebaseToken + Cf-JWT. Connecting automatically joins a quick game.
No browser or Tampermonkey required.
"""

import asyncio
from logger import BotLogger
import json
import os
import re
import time
import uuid
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

from ws_transport import WSTransport, check_token_expiry
from belief_engine import BeliefEngine
from slang import expand_slang


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class PlayerState:
    def __init__(self, data: dict):
        self.id: str = data.get("id", "")
        self.username: str = data.get("username", "?")
        self.level: int = data.get("level", 0)
        self.grid_idx: Optional[int] = data.get("gridIdx")
        self.is_alive: bool = data.get("isAlive", True)
        self.connection_status: str = data.get("connectionStatus", "connected")
        self.known_role: Optional[str] = None

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "level": self.level,
            "slot": self.grid_idx + 1 if self.grid_idx is not None else None,
            "is_alive": self.is_alive,
            "status": self.connection_status,
            "role": self.known_role
        }

    def __repr__(self):
        slot = self.grid_idx + 1 if self.grid_idx is not None else "?"
        return f"<Player [{slot}] {self.username} ({'alive' if self.is_alive else 'dead'})>"


class GameState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.game_id: Optional[str] = None
        self.status: str = "lobby"
        self.phase: str = ""
        self.day: int = 0
        self.my_player_id: Optional[str] = None
        self.my_role: Optional[str] = None
        self.players: Dict[str, PlayerState] = {}
        self.chat_history: List[dict] = []
        self.roles_in_game: List[str] = []
        self.event_timeline: List[dict] = []
        self.last_scan_target_id: Optional[str] = None
        self.last_detective_targets: List[str] = [] # [id_a, id_b]
        self.witch_potions = {"kill": 1, "protect": 1}
        self.harlot_visits = [] # List of slot numbers
        self.harlot_announced_targets = [] # The [A, B] announced in morning
        self.junior_target_id = None
        self.guardian_target_id = None
        self.last_detective_result = None
        self.last_aura_result = None
        self.cc_history = set() # Set of slot numbers
        self.headhunter_target_id = None
        self.medium_revives = 1
        self.priest_water_count = 1
        self.jailed_player_id = None
        self.warden_jailed_with = None  # ID of the other player in our cell
        self.warden_weapon_given = False  # Whether the weapon has been dropped
        self.votes: Dict[str, str] = {} # voter_id -> target_id
        self.wolf_votes: Dict[str, str] = {} # voter_id -> target_id

    def to_dict(self):
        return {
            "meta": {
                "game_id": self.game_id,
                "status": self.status,
                "phase": self.phase,
                "day": self.day
            },
            "me": {
                "id": self.my_player_id,
                "role": self.my_role
            },
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "timeline": self.event_timeline,
            "roles_in_game": self.roles_in_game,
            "witch_potions": self.witch_potions,
            "harlot_visits": self.harlot_visits,
            "harlot_announced_targets": self.harlot_announced_targets,
            "junior_target_id": self.junior_target_id,
            "guardian_target_id": self.guardian_target_id,
            "medium_revives": self.medium_revives,
            "priest_water_count": self.priest_water_count
        }

    @property
    def alive_players(self) -> List[PlayerState]:
        return [p for p in self.players.values() if p.is_alive]

    @property
    def dead_players(self) -> List[PlayerState]:
        return [p for p in self.players.values() if not p.is_alive]

    @property
    def is_alive(self) -> bool:
        me = self.players.get(self.my_player_id)
        return me.is_alive if me else False


class MatchLogger:
    def __init__(self, log_dir: str = "game_logs"):
        self.log_dir = log_dir
        self.log_file = os.path.join(log_dir, "bridge_capture.jsonl")
        os.makedirs(log_dir, exist_ok=True)

    def start_new_game(self, game_id: str):
        self.log_file = os.path.join(self.log_dir, f"game_{game_id}.jsonl")

    def log(self, direction: str, raw_data: str):
        try:
            entry = {"timestamp": int(time.time() * 1000), "type": direction, "data": raw_data}
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            pass


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class WolvesvilleClient:
    def __init__(
        self,
        bot_username: Optional[str] = None,
        game_id: Optional[str] = None,
        game_mode: str = "en",
        log_dir: str = "game_logs",
        verbose: bool = True,
        config_path: str = "config.json",
        reconnect: bool = True,
    ):
        self.bot_username = bot_username
        self.game_id = game_id
        self.game_mode = game_mode
        self.verbose = verbose
        self.config_path = config_path
        self.reconnect = reconnect

        self.state = GameState()
        self.logger = MatchLogger(log_dir)
        self.is_connected = False

        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
        self._ws: Optional[WSTransport] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        self.belief_engine = BeliefEngine(self)
        
        # Load tokens and config
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            self.firebase_token = config.get("WOLVESVILLE_TOKEN", "")
            self.cf_jwt = config.get("WOLVESVILLE_CF_JWT", "")
            cfg_username = config.get("BOT_USERNAME")
            if not self.bot_username:
                self.bot_username = cfg_username
            else:
                if isinstance(self.bot_username, list):
                    if cfg_username and cfg_username not in self.bot_username:
                        self.bot_username.append(cfg_username)
                else:
                    if cfg_username and cfg_username != self.bot_username:
                        self.bot_username = [self.bot_username, cfg_username]

    # ------------------------------------------------------------------
    # Event System
    # ------------------------------------------------------------------

    def on(self, event_name: str):
        def decorator(fn: Callable):
            self._callbacks[event_name].append(fn)
            return fn
        return decorator

    async def _fire(self, event_name: str, payload: dict):
        for fn in self._callbacks.get(event_name, []):
            try:
                await fn(payload)
            except Exception as e:
                BotLogger.danger(f"Callback error '{event_name}': {e}")

    # ------------------------------------------------------------------
    # Bridge Integration
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        """Load connection config from config.json."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    async def connect(self):
        """Connect directly to the Wolvesville game WebSocket."""
        cfg = self._load_config()
        firebase_token = cfg.get("WOLVESVILLE_TOKEN", "")
        cf_jwt = cfg.get("WOLVESVILLE_CF_JWT", "")
        
        # Use config game_mode only if self.game_mode is the default "en"
        game_mode = self.game_mode
        if game_mode == "en" and "GAME_MODE" in cfg:
            game_mode = cfg["GAME_MODE"]
            
        build_version = int(cfg.get("BUILD_VERSION", 79))

        # Warn if tokens are expired before even trying
        BotLogger.info("Checking token validity...")
        check_token_expiry(firebase_token, cf_jwt)

        self._ws = WSTransport(
            firebase_token=firebase_token,
            cf_jwt=cf_jwt,
            game_mode=game_mode,
            game_id=self.game_id,
            build_version=build_version,
            reconnect=self.reconnect,
        )
        self._ws.on_message = self._on_ws_message
        self._ws.on_connect = self._on_ws_connect
        self._ws.on_disconnect = self._on_ws_disconnect

        # This blocks and runs the reconnect loop
        await self._ws.connect()

    async def update_tokens(self, firebase_token: str, cf_jwt: str):
        """Hot-swap tokens on the running transport.
        
        The new tokens will be used on the next reconnect cycle.
        If we're currently in a game, they take effect when the game ends
        and the bot reconnects for a new game.
        """
        if self._ws:
            self._ws.firebase_token = firebase_token
            self._ws.cf_jwt = cf_jwt
            if self.verbose:
                print("[CLIENT] 🔑 Tokens updated (will use on next connect)")

    async def disconnect(self):
        """Disconnect the WebSocket and stop the client."""
        self._stop_heartbeat()
        if self._ws:
            await self._ws.disconnect()
        self.is_connected = False

    async def _on_ws_connect(self):
        """Called when the Socket.IO connection is established."""
        self.is_connected = True
        if self.verbose:
            BotLogger.client(f"✅ Game server connected!")

    async def _on_ws_disconnect(self):
        """Called when the WebSocket drops."""
        self.is_connected = False
        self._stop_heartbeat()
        if self.verbose:
            BotLogger.info("WebSocket disconnected.")

    async def _on_ws_message(self, raw_body: str):
        """Called by WSTransport for every 42[...] frame received."""
        try:
            raw_data = raw_body.strip()
            self.logger.log("receive", raw_data)
            await self._parse_socket_message(raw_data)
        except Exception as e:
            BotLogger.danger(f"WS message error: {e}")

    def _is_me(self, username):
        if not self.bot_username: return False
        if isinstance(self.bot_username, list):
            return username in self.bot_username
        return username == self.bot_username

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _start_heartbeat(self, initial_delay: float = 2.5, interval: float = 30.0):
        """Start sending player-heartbeat events to keep the connection alive."""
        self._stop_heartbeat()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(initial_delay, interval)
        )

    def _stop_heartbeat(self):
        """Cancel the heartbeat task."""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

    async def _heartbeat_loop(self, initial_delay: float, interval: float):
        """Send player-heartbeat on a schedule."""
        try:
            await asyncio.sleep(initial_delay)
            while True:
                if self._ws and self.is_connected:
                    raw = '42["player-heartbeat"]'
                    await self._ws.send(raw)
                    self.logger.log("send", raw)
                    if self.verbose:
                        print("[CLIENT] ♥ Heartbeat sent")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            BotLogger.danger(f"Heartbeat error: {e}")

    async def _parse_socket_message(self, raw: str):
        """Unpack Socket.IO framing: 42[\"event\", payload]"""
        if not raw.startswith("42"):
            return
        try:
            data = json.loads(raw[2:])
            event = data[0]
            payload = data[1] if len(data) > 1 else {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except:
                    pass
            await self._route_event(event, payload)
            # Save the "Bus" to a file for the user/BT to watch
            self._save_live_state()
        except Exception as e:
            if self.verbose:
                BotLogger.danger(f"Parse error: {e}")

    # ------------------------------------------------------------------
    # Inbound Event Router
    # ------------------------------------------------------------------

    async def _route_event(self, event: str, payload: dict):
        s = self.state

        if event == "game-joined":
            s.reset()
            s.game_id = payload.get("gameId")
            s.status = "playing"
            self.belief_engine.reset()
            self.logger.start_new_game(s.game_id)
            if self.verbose:
                BotLogger.client(f"Joined game: {s.game_id}")
            await self._fire("game_joined", {"game_id": s.game_id})

        elif event == "game-settings-changed":
            if "roles" in payload:
                s.roles_in_game = payload["roles"]
                event_obj = {"type": "SETTINGS_CHANGE", "roles": s.roles_in_game}
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)

        elif event == "players-and-equipped-items":
            s.players.clear()
            for p in payload.get("players", []):
                s.players[p["id"]] = PlayerState(p)
                if self._is_me(p.get("username")):
                    s.my_player_id = p["id"]
            if self.verbose:
                BotLogger.client(f"Tracking {len(s.players)} players.")

        elif event == "player-joined-and-equipped-items":
            p_data = payload.get("player", payload)
            p_id = p_data.get("id")
            if p_id:
                s.players[p_id] = PlayerState(p_data)

        elif event == "game-headhunter-set-target":
            target_id = payload.get("targetPlayerId")
            s.headhunter_target_id = target_id
            if target_id and target_id in s.players:
                p = s.players[target_id]
                slot = p.grid_idx + 1 if p.grid_idx is not None else "?"
                if self.verbose: BotLogger.belief(f"Headhunter target assigned: Slot {slot}")

        elif event == "game-starting":
            if self.verbose:
                BotLogger.client("Game starting...")
            await self._fire("game_starting", {})

        elif event == "game-started":
            s.status = "in-game"
            s.my_role = payload.get("role")
            if self.verbose:
                BotLogger.client(f"Game started! Role: {s.my_role}")
            active_ids = set()
            for p_data in payload.get("players", []):
                p_id = p_data["id"]
                active_ids.add(p_id)
                if p_id in s.players:
                    s.players[p_id].grid_idx = p_data.get("gridIdx")
                    s.players[p_id].is_alive = p_data.get("isAlive", True)
                else:
                    s.players[p_id] = PlayerState(p_data)
                if self._is_me(p_data.get("username")):
                    s.my_player_id = p_id
            for stale in [k for k in s.players if k not in active_ids]:
                del s.players[stale]
            event_obj = {"type": "GAME_STARTED", "my_role": s.my_role}
            s.event_timeline.append(event_obj)
            self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("game_started", {"my_role": s.my_role})

            # Start heartbeat IMMEDIATELY when game begins
            self._start_heartbeat(initial_delay=7.0, interval=30.0)

            s.phase = payload.get("phase", "")
            s.day = payload.get("day", s.day)
            if self.verbose:
                BotLogger.client(f"Phase → {s.phase} (day {s.day})")
            event_obj = {"type": "PHASE_CHANGE", "phase": s.phase, "day": s.day}
            s.event_timeline.append(event_obj)
            self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("phase_change", {"phase": s.phase, "day": s.day})

        elif event == "game-role-changed":
            p_id = payload.get("playerId")
            new_role = payload.get("roleId", "").lower().replace(" ", "-").replace("_", "-")
            old_role = payload.get("originalRoleId", "").lower().replace(" ", "-").replace("_", "-")
            
            if p_id in s.players:
                p = s.players[p_id]
                p.role = new_role
                p.known_role = new_role
                slot = p.grid_idx + 1 if p.grid_idx is not None else "?"
                
                if self.verbose:
                    BotLogger.belief(f"Role change detected: Slot {slot} changed from {old_role} to {new_role}")
                
                if p_id == s.my_player_id:
                    s.my_role = new_role
                    if self.verbose:
                        BotLogger.info(f"Bot role transformed to {new_role}!")
                        
                event_obj = {
                    "type": "ROLE_CHANGED",
                    "slot": slot,
                    "old_role": old_role,
                    "new_role": new_role,
                    "source": event
                }
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)

        elif event == "game-werewolves-set-roles":
            wolves = payload.get("werewolves", {})
            for pid, role in wolves.items():
                if pid in s.players:
                    s.players[pid].known_role = role
                    event_obj = {
                        "type": "TEAMMATE_REVEAL", 
                        "slot": s.players[pid].grid_idx + 1, 
                        "role": role
                    }
                    s.event_timeline.append(event_obj)
                    self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("teammates_revealed", {"wolves": wolves})

        elif event == "game-night-started":
            s.phase = "night"
            s.day = payload.get("day", s.day)
            if self.verbose:
                BotLogger.client(f"Phase → night (day {s.day})")
            event_obj = {"type": "PHASE_CHANGE", "phase": "night", "day": s.day}
            s.event_timeline.append(event_obj)
            self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("phase_change", {"phase": "night", "day": s.day})

        elif event == "game-day-started":
            s.phase = "day-discussion"
            s.day = payload.get("day", s.day)
            s.jailed_player_id = None
            s.votes = {} # Reset votes for the new day
            if self.verbose:
                BotLogger.client(f"Phase → day-discussion (day {s.day})")
            event_obj = {"type": "PHASE_CHANGE", "phase": "day-discussion", "day": s.day}
            s.event_timeline.append(event_obj)
            self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("phase_change", {"phase": "day-discussion", "day": s.day})

        elif event == "game-day-voting-started":
            s.phase = "day-voting"
            event_obj = {"type": "PHASE_CHANGE", "phase": "day-voting", "day": s.day}
            s.event_timeline.append(event_obj)
            self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("phase_change", {"phase": "day-voting", "day": s.day})

        elif event == "game-players-killed":
            victims = []
            for v in payload.get("victims", []):
                vid = v.get("targetPlayerId")
                vrole = v.get("targetPlayerRole")
                if vid in s.players:
                    s.players[vid].is_alive = False
                    s.players[vid].known_role = vrole
                    victims.append({"id": vid, "username": s.players[vid].username, "role": vrole})
            if self.verbose and victims:
                for v in victims:
                    BotLogger.info(f"Died: {v['username']} ({v['role']})")
                    # Add to the Unified Bus for the Belief Engine
                    slot = s.players[v['id']].grid_idx + 1 if v['id'] in s.players else None
                    event_obj = {
                        "type": "DEATH", 
                        "slot": slot, 
                        "role": v['role']
                    }
                    s.event_timeline.append(event_obj)
                    self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("players_killed", {"victims": victims})

        elif event in ("game-game-over", "game-over"):
            s.status = "lobby"
            self._stop_heartbeat()  # No heartbeat needed in lobby
            
            # Extract winner from gameResult payload
            winner = "Unknown"
            if "gameResult" in payload:
                status = payload["gameResult"].get("status", "")
                if status.startswith("STATUS_GAME_OVER_WINNER_"):
                    winner = status.replace("STATUS_GAME_OVER_WINNER_", "")
                else:
                    winner = status
            elif payload.get("winner"):
                winner = payload.get("winner")
            elif payload.get("winnerTeam"):
                winner = payload.get("winnerTeam")
                
            if self.verbose:
                BotLogger.info(f"Game over. Winner: {winner}")
            s.event_timeline.append({"type": "GAME_OVER", "winner": winner})
            await self._fire("game_over", {"winner": winner})

        elif event == "game-over-awards-available":
            award_data = payload.get("playerAward", {})
            total_xp = award_data.get("awardedTotalXp", 0)
            if self.verbose:
                BotLogger.client(f"✨ Earned {total_xp} XP from match!")
            await self._fire("xp_awarded", {"xp": total_xp, "details": award_data})

        elif event in ("error-game-started", "error-game-full", "error-game-not-found", "game-join-failed", "error-host-left-game", "error-game-wrong-password"):
            error_msg = event
            if isinstance(payload, dict) and payload.get("message"):
                error_msg = payload.get("message")
            if self.verbose:
                BotLogger.warning(f"Join Error: {error_msg}")
            self._stop_heartbeat()
            await self._fire("join_error", {"error": error_msg})

        elif event == "host-changed":
            # Per user request: treat host-changed as a join error in all cases,
            # triggering an immediate leave and retry.
            await self._fire("join_error", {"error": "error-host-changed"})

        elif event == "game-day-vote-set":
            voter_id = payload.get("playerId")
            target_id = payload.get("targetPlayerId")
            weight = payload.get("count", 1)
            
            if voter_id and target_id:
                s.votes[voter_id] = {"target_id": target_id, "weight": weight}
            
            voter = s.players[voter_id].username if voter_id in s.players else "Unknown"
            target = s.players[target_id].username if target_id in s.players else "Unknown"
            
            if self.verbose:
                v_p = s.players.get(voter_id)
                t_p = s.players.get(target_id)
                v_slot = f"[{v_p.grid_idx+1}] " if v_p and v_p.grid_idx is not None else ""
                t_slot = f"[{t_p.grid_idx+1}] " if t_p and t_p.grid_idx is not None else ""
                BotLogger.info(f"🗳️ {v_slot}{voter} voted for {t_slot}{target} (weight: {weight})")

            event_obj = {"type": "VOTE", "voter": voter, "target": target}
            s.event_timeline.append(event_obj)
            self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("vote_cast", {"voter_id": voter_id, "target_id": target_id})
            await self._evaluate_guardian_wolf_action()

        elif event == "game-day-vote-remove":
            voter_id = payload.get("playerId")
            if voter_id in s.votes:
                del s.votes[voter_id]
            
            voter = s.players[voter_id].username if voter_id in s.players else "Unknown"
            if self.verbose:
                v_p = s.players.get(voter_id)
                v_slot = f"[{v_p.grid_idx+1}] " if v_p and v_p.grid_idx is not None else ""
                BotLogger.info(f"🚫 {v_slot}{voter} removed their vote.")

            event_obj = {"type": "UNVOTE", "voter": voter}
            s.event_timeline.append(event_obj)
            self.belief_engine.process_event(event_obj, s.game_id)
            await self._fire("vote_removed", {"voter_id": voter_id})
            await self._evaluate_guardian_wolf_action()

        elif event == "game-werewolves-vote-set":
            voter_id = payload.get("playerId")
            target_id = payload.get("targetPlayerId")
            weight = payload.get("count", 1)
            
            if voter_id and target_id:
                s.wolf_votes[voter_id] = {"target_id": target_id, "weight": weight}
            
            if self.verbose:
                v_p = s.players.get(voter_id)
                t_p = s.players.get(target_id)
                voter = v_p.username if v_p else "Unknown"
                target = t_p.username if t_p else "Unknown"
                v_slot = f"[{v_p.grid_idx+1}] " if v_p and v_p.grid_idx is not None else ""
                t_slot = f"[{t_p.grid_idx+1}] " if t_p and t_p.grid_idx is not None else ""
                BotLogger.info(f"🐺 {v_slot}{voter} voted to kill {t_slot}{target} (weight: {weight})")

            await self._fire("wolf_vote_cast", {"voter_id": voter_id, "target_id": target_id})

        elif event == "game-werewolves-vote-remove":
            voter_id = payload.get("playerId")
            if voter_id in s.wolf_votes:
                del s.wolf_votes[voter_id]
            
            if self.verbose:
                v_p = s.players.get(voter_id)
                voter = v_p.username if v_p else "Unknown"
                v_slot = f"[{v_p.grid_idx+1}] " if v_p and v_p.grid_idx is not None else ""
                BotLogger.info(f"🚫 {v_slot}{voter} retracted wolf vote.")

            await self._fire("wolf_vote_removed", {"voter_id": voter_id})

        elif event == "game:chat-public:msg":
            author_id = payload.get("authorId", "system")
            text = payload.get("msg", "")
            is_system = "msgKey" in payload
            author_name = "System"
            if author_id in s.players:
                p = s.players[author_id]
                slot = f"[{p.grid_idx+1}] " if p.grid_idx is not None else ""
                author_name = f"{slot}{p.username}"
            if is_system:
                event_obj = {
                    "type": "SYSTEM",
                    "msg_key": payload.get("msgKey"),
                    "msg_args": payload.get("msgArgs", {})
                }
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)
                clean_text = text
            else:
                clean_text = expand_slang(text)
                event_obj = {"type": "CHAT", "speaker": author_name, "message": clean_text}
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)
                if self.verbose:
                    BotLogger.info(f"[CHAT] {author_name}: {clean_text}")
            
            entry = {
                "author_id": author_id,
                "author_name": author_name,
                "text": clean_text,
                "is_system": is_system,
                "msg_key": payload.get("msgKey", ""),
            }
            await self._fire("chat_message", entry)
 
        elif event == "game:chat-werewolves:msg":
            is_system = "msgKey" in payload
            if is_system:
                event_obj = {
                    "type": "SYSTEM",
                    "msg_key": payload.get("msgKey"),
                    "msg_args": payload.get("msgArgs", {})
                }
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)

            await self._fire("wolf_chat_message", {
                "author_id": payload.get("authorId"),
                "text": payload.get("msg", ""),
                "is_system": is_system
            })

        elif event == "game-wolf-seer-view-role":
            tid = payload.get("targetPlayerId")
            role = payload.get("role")
            if tid in s.players:
                s.players[tid].known_role = role
                event_obj = {
                    "type": "REVEAL",
                    "slot": s.players[tid].grid_idx + 1,
                    "role": role,
                    "source": event
                }
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)

        elif event == "game-aura-seer-view-role":
            aura = payload.get("result")
            tid = s.last_scan_target_id
            if tid and tid in s.players and aura:
                p = s.players[tid]
                slot = p.grid_idx + 1 if p.grid_idx is not None else "?"
                s.last_aura_result = {"slot": slot, "aura": aura}
                event_obj = {
                    "type": "AURA",
                    "slot": slot,
                    "aura": aura,
                    "source": event
                }
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)

        elif event == "game-seer-view-role":
            role = payload.get("role") or payload.get("result")
            tid = payload.get("targetPlayerId") or s.last_scan_target_id
            if tid and tid in s.players and role:
                p = s.players[tid]
                slot = p.grid_idx + 1 if p.grid_idx is not None else "?"
                s.last_aura_result = {"slot": slot, "role": role}
                event_obj = {
                    "type": "REVEAL",
                    "slot": slot,
                    "role": role,
                    "source": event
                }
                s.event_timeline.append(event_obj)
                self.belief_engine.process_event(event_obj, s.game_id)

        elif event == "game-jailer-jail-player":
            tid = payload.get("targetPlayerId")
            if tid:
                s.jailed_player_id = tid
                BotLogger.bt(f"Server confirmed: Player {tid} is JAILED.")
        
        elif event == "game-detective-result":
            are_equal = payload.get("areTeamsEqual")
            targets = s.last_detective_targets
            if targets and len(targets) == 2 and are_equal is not None:
                p1 = s.players.get(targets[0])
                p2 = s.players.get(targets[1])
                if p1 and p2:
                    event_obj = {
                        "type": "TEAM_RESULT",
                        "slot1": p1.grid_idx + 1,
                        "slot2": p2.grid_idx + 1,
                        "are_equal": are_equal,
                        "source": event
                    }
                    s.event_timeline.append(event_obj)
                    s.last_detective_result = event_obj
                    self.belief_engine.process_event(event_obj, s.game_id)
        
        elif event == "game-witch-set-state":
            # No longer relying on server state for witch potions
            pass
        
        elif event == "game-priest-set-state":
            # No longer relying on server state for priest water
            pass

        elif event == "player-disconnected":
            p_id = payload.get("id")
            if p_id in s.players:
                if payload.get("isAlive") is False or payload.get("suicide"):
                    s.players[p_id].is_alive = False
                else:
                    s.players[p_id].connection_status = "disconnected"
        elif event == "game-cupid-lover-ids-and-roles":
            lover_ids = payload.get("loverPlayerIds", [])
            lover_roles = payload.get("loverRoles", [])
            for pid, role in zip(lover_ids, lover_roles):
                if pid in s.players:
                    # Skip if we already know this role to avoid spam
                    if s.players[pid].known_role == role:
                        continue
                        
                    s.players[pid].known_role = role
                    slot = s.players[pid].grid_idx + 1 if s.players[pid].grid_idx is not None else "?"
                    event_obj = {
                        "type": "REVEAL",
                        "slot": slot,
                        "role": role,
                        "source": event
                    }
                    s.event_timeline.append(event_obj)
                    self.belief_engine.process_event(event_obj, s.game_id)
                    if self.verbose:
                        BotLogger.belief(f"Couple reveal: Slot {slot} is {role}")
            await self._fire("cupid_lovers_revealed", {"lover_ids": lover_ids, "lover_roles": lover_roles})
        
        elif event == "game-junior-werewolf-selected-player":
            tid = payload.get("targetPlayerId")
            s.junior_target_id = tid
            if self.verbose and tid in s.players:
                p = s.players[tid]
                slot = p.grid_idx + 1 if p.grid_idx is not None else "?"
                BotLogger.belief(f"Junior Werewolf target set: Slot {slot}")

        elif event == "game-shadow-wolf-double-votes":
            if self.verbose:
                BotLogger.info("Shadow Wolf activated double votes!")
            if s.my_role and s.my_role.lower().replace(" ", "-").replace("_", "-") == "flower-child":
                target_id = self.get_most_valuable_villager()
                if target_id:
                    p = s.players.get(target_id)
                    slot = p.grid_idx + 1 if p and p.grid_idx is not None else "?"
                    if self.verbose:
                        BotLogger.info(f"Flower Child immediately selecting most valuable villager: Slot {slot}")
                    await self.flower_child_select(target_id)


        elif event == "game-warden-self-jailed":
            other_id = payload.get("otherPlayerId")
            day = payload.get("day")
            s.warden_jailed_with = other_id
            if self.verbose:
                if other_id and other_id in s.players:
                    p = s.players[other_id]
                    slot = p.grid_idx + 1 if p.grid_idx is not None else "?"
                    BotLogger.info(f"🔒 Warden jailed us with Slot {slot} on day {day}")
                else:
                    BotLogger.info(f"🔒 Warden jailed us with {other_id} on day {day}")
            await self._fire("warden_jailed", {"other_id": other_id, "day": day})

        elif event == "game-warden-weapon-given":
            day = payload.get("day")
            s.warden_weapon_given = True
            if self.verbose:
                BotLogger.info(f"⚔️ Warden dropped the weapon in jail (day {day})")
            await self._fire("warden_weapon_given", {"day": day})

        elif event == "game:chat-warden:msg":
            # Message received in warden jail cell
            author_id = payload.get("authorId")
            msg_text = payload.get("msg", "")
            msg_key = payload.get("msgKey", "")
            msg_args = payload.get("msgArgs", {})
            is_system = (author_id == "system")
            
            if is_system:
                if self.verbose:
                    BotLogger.info(f"🔒 [JAIL SYSTEM] {msg_key}: {msg_args}")
            else:
                author_name = "Unknown"
                if author_id in s.players:
                    p = s.players[author_id]
                    slot = p.grid_idx + 1 if p.grid_idx is not None else "?"
                    author_name = f"[{slot}] {p.username}"
                if self.verbose:
                    BotLogger.info(f"🔒 [JAIL CHAT] {author_name}: {msg_text}")
                    
                # Clean and extract claims just like public chat
                clean_text = expand_slang(msg_text) if not is_system else msg_text
                entry = {
                    "author_id": author_id,
                    "author_name": author_name,
                    "text": clean_text,
                    "is_system": is_system,
                    "msg_key": msg_key,
                }
                await self._fire("chat_message", entry)
            await self._fire("warden_chat_received", {"author_id": author_id, "msg": msg_text, "msg_key": msg_key, "msg_args": msg_args, "is_system": is_system})

        # (Events are now fed to belief_engine immediately as they are generated above)
        pass

    # ------------------------------------------------------------------
    # Outbound Actions (injected into browser via bridge)
    # ------------------------------------------------------------------

    def _resolve_id(self, target) -> Optional[str]:
        t = str(target).strip()
        if t in self.state.players:
            return t
        for p_id, p in self.state.players.items():
            if t.isdigit() and p.grid_idx == int(t) - 1:
                return p_id
            if p.username.lower() == t.lower():
                return p_id
        return None

    async def _emit(self, event: str, payload_obj) -> bool:
        if not self.is_connected or not self._ws:
            BotLogger.warning(f"Not connected — cannot emit '{event}'")
            return False

        # The game server is strict about wire format — must match exactly.
        # Real wire format: 42["event","{\"key\":\"val\",\"k2\":\"v2\"}"]
        #   - The payload is a STRINGIFIED JSON string, not a raw object
        #   - Compact separators inside the stringified payload (no spaces)
        #   - This matches what the real browser client sends
        if payload_obj is None or payload_obj == {}:
            raw = f'42["{event}"]'
        else:
            # Double-encode: first stringify the object with compact separators,
            # then embed that string as a JSON string element in the array
            payload_str = json.dumps(payload_obj, separators=(',', ':'))
            raw = f'42["{event}",{json.dumps(payload_str)}]'

        success = await self._ws.send(raw)
        if success:
            self.logger.log("send", raw)
            if self.verbose:
                BotLogger.client(f"→ {event}: {json.dumps(payload_obj)}")
        return success

    async def auto_queue(self):
        """
        Join a new quick game by reconnecting the WebSocket.
        Connecting to the game URL automatically queues for matchmaking.
        """
        print("[CLIENT] 🔄 Reconnecting WebSocket to queue for new game...")
        if self._ws:
            await self._ws.reconnect_for_new_game()
        return True

    async def auto_leave(self):
        """
        Leave the current game by closing the WebSocket.
        The server will register the disconnect as a player leave.
        The reconnect loop in WSTransport will then open a fresh connection.
        """
        print("[CLIENT] 🚪 Leaving game (closing WebSocket)...")
        self.state.reset()
        if self._ws:
            await self._ws.reconnect_for_new_game()
        return True

    async def _send_split_msg(self, event: str, text: str):
        limit = 140
        if len(text) <= limit:
            return await self._emit(event, {"msg": text, "pId": uuid.uuid4().hex[:6]})

        # Split logic
        parts = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                parts.append(remaining)
                break
            cut = remaining[:limit]
            # Try to split at sentence boundary first, then space
            split_idx = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
            if split_idx == -1 or split_idx < limit // 2:
                split_idx = cut.rfind(" ")
            
            if split_idx == -1: split_idx = limit
            else: split_idx += 1

            parts.append(remaining[:split_idx].strip())
            remaining = remaining[split_idx:].strip()

        for i, p in enumerate(parts):
            if i > 0: await asyncio.sleep(0.8) # small delay
            await self._emit(event, {"msg": p, "pId": uuid.uuid4().hex[:6]})
        return True

    async def send_message(self, text: str):
        return await self._send_split_msg("game:chat-public:msg", text)

    async def send_jailer_message(self, text: str):
        return await self._send_split_msg("game:chat-jailer:msg", text)

    async def send_warden_message(self, text: str):
        return await self._send_split_msg("game:chat-warden:msg", text)

    async def send_medium_message(self, text: str):
        return await self._send_split_msg("game-medium-chat", text)

    async def send_wolf_message(self, text: str):
        return await self._send_split_msg("game:chat-werewolves:msg", text)

    async def mayor_reveal(self):
        return await self._emit("mayor-reveal-role", {})

    async def decrease_discussion_time(self):
        """Clicks the 'Skip' / 'Decrease Time' button in custom games."""
        return await self._emit("game-discussion-time-decrease", {})

    async def vote(self, target):
        tid = self._resolve_id(target)
        if not tid:
            print(f"[CLIENT] vote: can't resolve '{target}'")
            return False
        return await self._emit("game-day-vote-set", {"targetPlayerId": tid})

    async def unvote(self):
        return await self._emit("game-day-vote-remove", {})

    def get_most_voted_player_id(self) -> Tuple[Optional[str], int]:
        """Returns (player_id, total_weight) for the player currently receiving the most votes."""
        counts = {}
        for vote_info in self.state.votes.values():
            target_id = vote_info["target_id"]
            weight = vote_info["weight"]
            counts[target_id] = counts.get(target_id, 0) + weight
        
        if not counts:
            return None, 0
            
        # Get target with max total weight
        most_voted_id = max(counts, key=counts.get)
        return most_voted_id, counts[most_voted_id]

    async def wolf_vote(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-werewolves-vote-set", {"targetPlayerId": tid, "count": 1})
        return False

    async def doctor_protect(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-doctor-protect-player", {"targetPlayerId": tid})
        return False

    async def bodyguard_protect(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-bodyguard-protect-player", {"targetPlayerId": tid})
        return False

    async def jailer_jail(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-jailer-jail-player", {"targetPlayerId": tid})
        return False

    async def jailer_chat(self, target_id, msg):
        return await self._emit("game:chat-jailer:msg", {"msg": msg, "pId": target_id})

    async def jailer_kill(self, target_id):
        return await self._emit("game-jailer-kill-player", {"targetPlayerId": target_id})

    async def warden_select(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-warden-select-target", tid)
        return False

    async def warden_give_weapon(self):
        return await self._emit("game-warden-give-weapon", {})

    async def warden_kill(self):
        return await self._emit("game-warden-kill", {})

    async def shadow_wolf_activate(self):
        return await self._emit("game-shadow-wolf-activate", {})

    async def aura_seer_scan(self, target):
        tid = self._resolve_id(target)
        if tid:
            self.state.last_scan_target_id = tid
            return await self._emit("game-aura-seer-view-role", {"targetPlayerId": tid})
        return False

    async def seer_scan(self, target):
        tid = self._resolve_id(target)
        if tid:
            self.state.last_scan_target_id = tid
            return await self._emit("game-seer-view-role", {"targetPlayerId": tid})
        return False

    async def detective_check(self, a, b):
        id_a = self._resolve_id(a)
        id_b = self._resolve_id(b)
        if id_a and id_b:
            self.state.last_detective_targets = [id_a, id_b]
            return await self._emit("game-detective-selected-targets", [id_a, id_b])
        return False

    async def vigilante_shoot(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-vigilante-shoot", {"targetPlayerId": tid})
        return False

    async def vigilante_reveal(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-vigilante-reveal", {"targetPlayerId": tid})
        return False

    async def witch_kill(self, target):
        tid = self._resolve_id(target)
        if tid and self.state.witch_potions["kill"] > 0:
            success = await self._emit("game-witch-kill-player", {"targetPlayerId": tid})
            if success:
                self.state.witch_potions["kill"] -= 1
            return success
        return False

    async def witch_protect(self, target):
        tid = self._resolve_id(target)
        if tid and self.state.witch_potions["protect"] > 0:
            success = await self._emit("game-witch-protect-player", {"targetPlayerId": tid})
            if success:
                self.state.witch_potions["protect"] -= 1
            return success
        return False

    async def priest_water(self, target):
        tid = self._resolve_id(target)
        if tid and self.state.priest_water_count > 0:
            success = await self._emit("game-priest-kill-player", {"targetPlayerId": tid})
            if success:
                self.state.priest_water_count -= 1
            return success
        return False


    async def beast_hunter_trap(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-beast-hunter-trap-placed", {"targetPlayerId": tid, "active": True})
        return False

    async def gunner_shoot(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-gunner-shoot-player", {"targetPlayerId": tid})
        return False

    async def marksman_shoot(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-marksman-shoot", {"targetPlayerId": tid})
        return False

    async def wolf_seer_scan(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-wolf-seer-view-role", {"targetPlayerId": tid})
        return False

    async def toxic_wolf_poison(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-toxic-wolf-poisoned", {"targetPlayerId": tid})
        return False

    async def nightmare_sleep(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-nightmare-werewolf-asleep-target-player", {"targetPlayerId": tid})
        return False

    async def sk_kill(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-serial-killer-vote", {"targetPlayerId": tid})
        return False

    async def arsonist_douse(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-arsonist-douse-player", {"targetPlayerId": tid})
        return False

    async def arsonist_ignite(self):
        return await self._emit("game-arsonist-ignite-players", {})

    async def junior_werewolf_select(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-junior-werewolf-selected-player", {"targetPlayerId": tid})
        return False

    async def flower_child_select(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-flower-child-selected-player", {"targetPlayerId": tid})
        return False

    async def guardian_wolf_select(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-guardian-wolf-selected-player", {"targetPlayerId": tid})
        return False

    async def harlot_visit(self, target):
        tid = self._resolve_id(target)
        if tid:
            # We also track it here for convenience
            p = self.state.players.get(tid)
            if p and p.grid_idx is not None:
                slot = p.grid_idx + 1
                if slot not in self.state.harlot_visits:
                    self.state.harlot_visits.append(slot)
            return await self._emit("game-harlot-select-player", {"targetPlayerId": tid})
        return False

    async def judge_select(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-judge-selected-player", {"targetPlayerId": tid})
        return False

    async def loudmouth_select(self, target):
        tid = self._resolve_id(target)
        if tid:
            return await self._emit("game-loudmouth-select-player", {"targetPlayerId": tid})
        return False

    async def alpha_growl(self, msg: str):
        return await self._emit("game-growl-alpha-werewolf", {"msg": msg})


    async def medium_revive(self, target):
        tid = self._resolve_id(target)
        if tid and self.state.medium_revives > 0:
            success = await self._emit("game-medium-revive-player", {"targetPlayerId": tid})
            if success:
                self.state.medium_revives -= 1
            return success
        return False

    # ------------------------------------------------------------------
    # REST API — Inventory & Talismans (headless, via curl)
    # ------------------------------------------------------------------

    async def _curl_api(self, url: str, method: str = "GET", data: dict = None) -> Optional[dict]:
        """Make a REST API call to core.api-wolvesville.com using curl."""
        cfg = self._load_config()
        token = cfg.get("WOLVESVILLE_TOKEN", "")
        cf_jwt = cfg.get("WOLVESVILLE_CF_JWT", "")

        cmd = [
            "curl", "-s",
            "--max-time", "15",
            "--connect-timeout", "5",
            "-X", method,
            "-H", f"authorization: Bearer {token}",
            "-H", f"cf-jwt: {cf_jwt}",
            "-H", "accept: application/json",
            "-H", "content-type: application/json",
            "-H", "ids: 1",
            "-H", "referer: https://www.wolvesville.com/",
            "-H", "origin: https://www.wolvesville.com",
            "-H", "user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        ]
        if data is not None:
            cmd += ["-d", json.dumps(data)]
        cmd.append(url)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0 and stdout:
                return json.loads(stdout.decode())
        except Exception as e:
            BotLogger.danger(f"API call failed: {e}")
        return None

    async def get_inventory(self) -> Optional[dict]:
        """Fetch the full player inventory from the REST API."""
        data = await self._curl_api("https://core.api-wolvesville.com/inventory")
        if data and "code" not in data:
            return data
        BotLogger.warning(f"Failed to fetch inventory: {data}")
        return None

    async def get_talismans(self) -> Optional[list]:
        """Fetch owned talismans and their equipped status."""
        inv = await self.get_inventory()
        if inv:
            talismans = inv.get("ownedTalismans", [])
            if self.verbose:
                for t in talismans:
                    eq = "✅" if t["equipped"] else "❌"
                    BotLogger.info(f"Talisman {t['talismanId']}: {eq} (count={t['count']}, str={t['strength']})")
            return talismans
        return None

    async def equip_talisman(self, talisman_id: str) -> bool:
        """Equip a talisman by its ID via the REST API.
        
        Args:
            talisman_id: The talisman ID to equip (e.g. 'zB4', 'pcj').
            
        Returns:
            True if the talisman was successfully equipped, False otherwise.
        """
        data = await self._curl_api(
            "https://core.api-wolvesville.com/inventory/talismans/equip",
            method="PUT",
            data={"talismanId": talisman_id},
        )
        if data and "code" not in data:
            # Verify from the response
            talismans = data.get("ownedTalismans", [])
            equipped = [t for t in talismans if t["talismanId"] == talisman_id and t["equipped"]]
            if equipped:
                BotLogger.info(f"✅ Talisman '{talisman_id}' equipped successfully!")
                return True
            else:
                BotLogger.warning(f"Equip request returned 200 but talisman '{talisman_id}' not marked equipped.")
                return False
        BotLogger.warning(f"Failed to equip talisman '{talisman_id}': {data}")
        return False

    async def _evaluate_guardian_wolf_action(self):
        s = self.state
        if s.my_role and s.my_role.lower().replace(" ", "-").replace("_", "-") == "guardian-wolf":
            if s.phase not in ("day-voting", "voting"):
                return
            
            from inference import ROLE_BUCKET
            
            # Count votes on each player
            vote_counts = {}
            for voter_id, vote_data in s.votes.items():
                tid = vote_data["target_id"]
                w = vote_data["weight"]
                vote_counts[tid] = vote_counts.get(tid, 0) + w
                
            # Find all alive wolves
            wolf_ids = []
            for p in s.alive_players:
                if p.id == s.my_player_id:
                    wolf_ids.append(p.id)
                elif p.known_role and ROLE_BUCKET.get(p.known_role.lower().replace(" ", "-").replace("_", "-")) == "wolf":
                    wolf_ids.append(p.id)
                        
            # Find the wolf with the highest votes (must be >= 1)
            highest_votes = 0
            target_wolf_id = None
            
            for wid in wolf_ids:
                v = vote_counts.get(wid, 0)
                if v > highest_votes:
                    highest_votes = v
                    target_wolf_id = wid
                    
            if target_wolf_id and highest_votes >= 1:
                if getattr(s, "guardian_target_id", None) != target_wolf_id:
                    s.guardian_target_id = target_wolf_id
                    p = s.players.get(target_wolf_id)
                    slot = p.grid_idx + 1 if p and p.grid_idx is not None else "?"
                    if self.verbose:
                        BotLogger.info(f"Guardian Wolf protecting wolf teammate: Slot {slot} with {highest_votes} votes.")
                    await self.guardian_wolf_select(target_wolf_id)

    def get_most_valuable_villager(self) -> Optional[str]:
        """Finds the most valuable villager still alive, excluding the bot themselves."""
        s = self.state
        matrix = self.belief_engine.matrix
        if not matrix:
            return None

        # Exclude ourselves
        alive_others = [p for p in s.alive_players if p.id != s.my_player_id and p.grid_idx is not None]
        if not alive_others:
            return None

        from inference import ROLE_BUCKET

        best_value = -1.0
        best_player_id = None

        for p in alive_others:
            slot = p.grid_idx + 1
            if slot in matrix:
                # Exclude confirmed/suspected wolves or solos
                if p.known_role:
                    role_key = p.known_role.lower().replace(" ", "-").replace("_", "-")
                    if ROLE_BUCKET.get(role_key) in ("wolf", "solo"):
                        continue

                # Calculate wolf and solo probability
                evil_prob = sum(prob for role, prob in matrix[slot].items() if ROLE_BUCKET.get(role) in ("wolf", "solo"))
                if evil_prob > 0.4:
                    continue

                # Calculate value score
                investigator_prob = sum(prob for role, prob in matrix[slot].items() if ROLE_BUCKET.get(role) == "village-investigator")
                verifiable_prob = sum(prob for role, prob in matrix[slot].items() if ROLE_BUCKET.get(role) == "village-verifiable")
                boring_prob = sum(prob for role, prob in matrix[slot].items() if ROLE_BUCKET.get(role) == "village-boring")

                value = investigator_prob * 2.0 + verifiable_prob * 1.5 + boring_prob * 1.0
                if value > best_value:
                    best_value = value
                    best_player_id = p.id

        return best_player_id

    def _save_live_state(self):
        """Dumps the current 'Bus' state to a JSON file for debugging."""
        try:
            with open("live_state.json", "w", encoding="utf-8") as f:
                json.dump(self.state.to_dict(), f, indent=4)
        except:
            pass

