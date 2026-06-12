import asyncio
import json
import os
import signal
import time
import aiohttp
from datetime import datetime
from aiohttp import web
from wolvesville_client import WolvesvilleClient
from logger import BotLogger, LOG_BUFFER
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.console import Console
from rich.layout import Layout

lobby_queue = asyncio.Queue()
seen_lobbies = set()
lobby_names = {}
STRICT_MODE = False

SMALL_CAPS = "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"
NORMAL_CHARS = "abcdefghijklmnopqrstuvwxyz"
FONT_TRANSLATOR = str.maketrans(SMALL_CAPS, NORMAL_CHARS)

WOLF_KEYWORDS = ["wolf", "werewolf", "sorcerer"]
SOLO_ROLES = ["fool", "headhunter", "anarchist", "serial-killer",
              "sect-leader", "cannibal", "corruptor", "bandit"]

class FarmerStats:
    def __init__(self):
        self.start_time = datetime.now()
        self.games_finished = 0
        self.total_xp = 0
        self.current_lobby = "Searching..."
        self.status = "Initializing"
        self.role = "Unknown"
        self.transition_times = []
        self.game_durations = []
        self.role_history = []  # list of role strings across all games
        
    @staticmethod
    def classify_role(role: str) -> str:
        """Classify a role as 'wolf', 'solo', or 'village'."""
        r = role.lower()
        if any(kw in r for kw in WOLF_KEYWORDS):
            return "wolf"
        if any(r == solo or r.startswith(solo) for solo in SOLO_ROLES):
            return "solo"
        return "village"

    def get_team_pcts(self):
        """Return (wolf%, village%, solo%, total) from role_history."""
        total = len(self.role_history)
        if total == 0:
            return 0, 0, 0, 0
        wolf = sum(1 for r in self.role_history if self.classify_role(r) == "wolf")
        solo = sum(1 for r in self.role_history if self.classify_role(r) == "solo")
        village = total - wolf - solo
        return (wolf / total * 100, village / total * 100, solo / total * 100, total)

    def add_game(self, xp=0):
        self.games_finished += 1
        self.total_xp += xp
        
    def add_transition_time(self, seconds):
        self.transition_times.append(seconds)
        
    def add_game_duration(self, seconds):
        self.game_durations.append(seconds)
        
    def get_xp_per_hour(self):
        elapsed = (datetime.now() - self.start_time).total_seconds()
        if elapsed < 1: return 0
        return (self.total_xp / elapsed) * 3600
        
    def generate_dashboard(self):
        # Stats Table
        stats_table = Table(box=None, expand=True)
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="bold white")
        
        elapsed = datetime.now() - self.start_time
        elapsed_str = str(elapsed).split(".")[0]
        
        if self.transition_times:
            avg_transition = sum(self.transition_times) / len(self.transition_times)
            avg_transition_str = f"{avg_transition:.1f}s"
        else:
            avg_transition_str = "N/A"
            
        if self.game_durations:
            avg_duration = sum(self.game_durations) / len(self.game_durations)
            avg_duration_str = f"{avg_duration:.1f}s"
        else:
            avg_duration_str = "N/A"
            
        wolf_pct, village_pct, solo_pct, role_total = self.get_team_pcts()
        if role_total > 0:
            team_str = f"[red]🐺 {wolf_pct:.0f}%[/] / [green]🏘️ {village_pct:.0f}%[/]"
            if solo_pct > 0:
                team_str += f" / [yellow]🎭 {solo_pct:.0f}%[/]"
            team_str += f"  [dim]({role_total} games)[/]"
        else:
            team_str = "N/A"

        stats_table.add_row("🕒 Uptime", elapsed_str)
        stats_table.add_row("🎮 Games", str(self.games_finished))
        stats_table.add_row("✨ Total XP", f"[bold green]{self.total_xp}[/]")
        stats_table.add_row("📈 Rate", f"[bold yellow]{self.get_xp_per_hour():.0f} XP/h[/]")
        stats_table.add_row("📍 Lobby", f"[blue]{self.current_lobby}[/]")
        stats_table.add_row("🎭 Role", f"[bold magenta]{self.role}[/]")
        stats_table.add_row("🐺 Teams", team_str)
        stats_table.add_row("⏱️ Avg Join Time", f"[bold cyan]{avg_transition_str}[/]")
        stats_table.add_row("⏳ Avg Game Time", f"[bold yellow]{avg_duration_str}[/]")
        stats_table.add_row("🚦 Status", f"[bold white]{self.status}[/]")
        
        # Log Panel
        log_content = "\n".join(LOG_BUFFER) if LOG_BUFFER else "Waiting for logs..."
        log_panel = Panel(log_content, title="[bold magenta]Recent Activity[/]", border_style="magenta", expand=True)
        
        # Combine in a layout
        layout = Layout()
        layout.split_column(
            Layout(Panel(stats_table, title="[bold green]Wolvesville EXP Farmer[/]", border_style="green"), name="stats", size=14),
            Layout(log_panel, name="logs")
        )
        
        return layout

stats = FarmerStats()

async def handle_lobbies(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    
    while not lobby_queue.empty():
        try:
            lobby_queue.get_nowait()
        except asyncio.QueueEmpty:
            break

    lobbies = data.get("lobbies") or data.get("openGames") or []
    added = 0
    for lobby in lobbies:
        raw_name = lobby.get("name", "")
        name = raw_name.lower().translate(FONT_TRANSLATOR)
        has_pwd = lobby.get("hasPassword", False)
        
        # Match if it contains "vill win" or the specific decorative characters/previous lobby name
        target_raw = "\u2726\u2022\u2508\u0e51 \u1d20\u026a\u029f\u029f \u1d21\u026a\u0274 \u1d07 \u0274\u1d1b \u0e51\u2508\u2022\u2726"
        target_translated = "\u2726\u2022\u2508\u0e51 vill win e nt \u0e51\u2508\u2022\u2726"
        
        if STRICT_MODE:
            is_match = target_raw.lower() in raw_name.lower() or target_translated in name
        else:
            is_match = "vill win" in name or target_raw.lower() in raw_name.lower() or target_translated in name
            
        if is_match:
            game_id = lobby.get("gameId")
            if not game_id or game_id in seen_lobbies:
                continue

            if lobby.get("playerCount", 0) < 16 and not has_pwd:
                await lobby_queue.put(game_id)
                seen_lobbies.add(game_id)
                lobby_names[game_id] = lobby.get("name", "Unknown Lobby")
                added += 1
                     
    return web.json_response({"status": "ok", "added": added})

async def start_lobby_server():
    app = web.Application()
    app.router.add_post("/lobbies", handle_lobbies)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 5589)
    await site.start()

async def fetch_custom_lobbies(firebase_token, cf_jwt):
    """Fetch custom games directly from the Wolvesville API using curl."""
    url = "https://game.api-wolvesville.com/api/public/game/custom?language=en"
    
    cmd = [
        "curl", "-s",
        "--max-time", "15",
        "--connect-timeout", "5",
        "-H", f"authorization: Bearer {firebase_token}",
        "-H", f"cf-jwt: {cf_jwt}",
        "-H", "ids: 1",
        "-H", "referer: https://www.wolvesville.com/",
        "-H", "user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        url
    ]
    
    try:
        with open("response_debug", "a") as f:
            f.write(f"\n[{datetime.now()}] Fetching lobbies via curl...\n")
            
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            data = json.loads(stdout.decode())
            with open("response_debug", "a") as f:
                f.write(f"[{datetime.now()}] Success: {json.dumps(data)}\n")
            return data
        else:
            with open("response_debug", "a") as f:
                f.write(f"[{datetime.now()}] Curl Error: {stderr.decode()}\n")
            return None
    except Exception as e:
        with open("response_debug", "a") as f:
            f.write(f"[{datetime.now()}] Exception: {str(e)}\n")
        return None

async def refresh_lobbies():
    """Trigger a one-time API poll to refresh the lobby queue."""
    try:
        with open("config.json", "r") as f:
            cfg = json.load(f)
        
        data = await fetch_custom_lobbies(cfg["WOLVESVILLE_TOKEN"], cfg["WOLVESVILLE_CF_JWT"])
        if data and ("lobbies" in data or "openGames" in data):
            # Create a mock request to reuse handle_lobbies logic
            class MockRequest:
                async def json(self): return data
            await handle_lobbies(MockRequest())
            return True
    except Exception:
        pass
    return False

async def get_next_farm_game_id():
    while True:
        stats.status = "Searching..."
        if lobby_queue.empty():
            await refresh_lobbies()
        
        try:
            # Wait up to 2 seconds for a lobby to appear in the queue
            game_id = await asyncio.wait_for(lobby_queue.get(), timeout=2.0)
            return game_id
        except asyncio.TimeoutError:
            # If no lobby appears, loop back and try refreshing again
            continue

from token_server import TokenServer

async def farm():
    BotLogger.capture_mode = True
    token_server = TokenServer(config_path="config.json")
    await token_server.start()
    await start_lobby_server()
    await refresh_lobbies()
    
    last_game_ended_time = None
    with Live(stats.generate_dashboard(), refresh_per_second=1) as live:
        while True:
            try:
                game_id = await get_next_farm_game_id()
                stats.current_lobby = lobby_names.get(game_id, game_id[:8] + "...")
                stats.role = "Unknown"
                stats.status = "Connecting"
                live.update(stats.generate_dashboard())
                
                client = WolvesvilleClient(game_id=game_id, game_mode="custom", verbose=False)
                game_over_event = asyncio.Event()
                awards_event = asyncio.Event()
                join_failed = False
                current_game_start_time = None
                
                game_joined_event = asyncio.Event()
                game_starting_event = asyncio.Event()
                
                @client.on("game_over")
                async def on_game_over(payload):
                    nonlocal current_game_start_time
                    stats.add_game()
                    if current_game_start_time is not None:
                        duration = time.time() - current_game_start_time
                        stats.add_game_duration(duration)
                        current_game_start_time = None
                    stats.status = "Match Over"
                    live.update(stats.generate_dashboard())
                    game_over_event.set()

                @client.on("game_joined")
                async def on_game_joined(payload):
                    nonlocal last_game_ended_time
                    if last_game_ended_time is not None:
                        transition = time.time() - last_game_ended_time
                        stats.add_transition_time(transition)
                        last_game_ended_time = None
                    game_joined_event.set()

                @client.on("xp_awarded")
                async def on_xp(payload):
                    xp = payload.get("xp", 0)
                    stats.total_xp += xp
                    live.update(stats.generate_dashboard())
                    awards_event.set()

                @client.on("join_error")
                async def on_join_error(payload):
                    nonlocal join_failed
                    stats.status = f"Join Failed: {payload.get('error')}"
                    live.update(stats.generate_dashboard())
                    join_failed = True
                    game_over_event.set()

                @client.on("game_starting")
                async def on_game_starting(payload):
                    game_starting_event.set()

                @client.on("game_started")
                async def on_game_started(payload):
                    nonlocal current_game_start_time
                    stats.role = payload.get("my_role", "Unknown")
                    if stats.role != "Unknown":
                        stats.role_history.append(stats.role)
                    live.update(stats.generate_dashboard())
                    current_game_start_time = time.time()
                    game_starting_event.set()

                has_said_me = False
                partner_id = None
                partner_role = None
                wolf_partner_id = None
                has_set_junior_target = False
                has_watered = False
                has_shot_gunner = False
                has_shot_vigilante = False
                has_messaged_partner_slot = False
                has_junior_teammate = False
                teammates_received = False

                @client.on("teammates_revealed")
                async def on_teammates(payload):
                    nonlocal has_junior_teammate, teammates_received, partner_id, has_messaged_partner_slot
                    teammates_received = True
                    wolves = payload.get("wolves", {})
                    is_junior = client.state.my_role and "junior-werewolf" in client.state.my_role.lower()
                    if not is_junior:
                        for pid, role in wolves.items():
                            if pid != client.state.my_player_id and "junior-werewolf" in role.lower():
                                has_junior_teammate = True
                                BotLogger.bt("Teammates: Detected Junior Werewolf teammate!")
                                if partner_id and not has_messaged_partner_slot:
                                    partner_player = client.state.players.get(partner_id)
                                    partner_number = partner_player.grid_idx + 1 if (partner_player and partner_player.grid_idx is not None) else None
                                    if partner_number:
                                        has_messaged_partner_slot = True
                                        BotLogger.bt(f"Lovers: Teammate is Junior WW! Canceling vote and posting partner slot {partner_number} in wolf chat.")
                                        await client._emit("game-werewolves-vote-remove", {})
                                        await asyncio.sleep(1)
                                        await client.send_wolf_message(str(partner_number))
                                break

                @client.on("vote_cast")
                async def on_vote(payload):
                    nonlocal has_watered, has_shot_gunner, has_shot_vigilante
                    role = client.state.my_role
                    if not role: return
                    target_id = payload.get("target_id")
                    if not target_id or target_id == client.state.my_player_id:
                        return
                    v_count = sum(v["weight"] for v in client.state.votes.values() if v["target_id"] == target_id)
                    if v_count >= 2:
                        if not has_watered and "priest" in role.lower():
                            has_watered = True
                            BotLogger.bt(f"Priest: Watering player {target_id} immediately (hit {v_count} votes)")
                            await client.priest_water(target_id)
                        if not has_shot_gunner and "gunner" in role.lower():
                            has_shot_gunner = True
                            BotLogger.bt(f"Gunner: Shooting player {target_id} (hit {v_count} votes)")
                            await client.gunner_shoot(target_id)
                        if not has_shot_vigilante and "vigilante" in role.lower():
                            has_shot_vigilante = True
                            BotLogger.bt(f"Vigilante: Shooting player {target_id} (hit {v_count} votes)")
                            await client.vigilante_shoot(target_id)

                @client.on("wolf_vote_cast")
                async def on_wolf_vote(payload):
                    nonlocal has_set_junior_target
                    if has_set_junior_target: return
                    
                    voter_id = payload.get("voter_id")
                    target_id = payload.get("target_id")
                    
                    role = client.state.my_role
                    if role and "junior-werewolf" in role.lower():
                        # Target select the first person voted by a teammate (different than bot's id)
                        if voter_id != client.state.my_player_id:
                            await asyncio.sleep(1) # Small delay for realism
                            BotLogger.bt(f"Junior Werewolf selecting target from teammate vote: {target_id}")
                            await client.junior_werewolf_select(target_id)
                            if not partner_id:
                                await client.wolf_vote(target_id)
                            else:
                                BotLogger.bt("Junior Werewolf is coupled, so keeping the vote for the partner.")
                            has_set_junior_target = True

                @client.on("wolf_chat_message")
                async def on_wolf_chat(payload):
                    nonlocal has_set_junior_target
                    if has_set_junior_target: return

                    role = client.state.my_role
                    if not role or "junior-werewolf" not in role.lower(): return

                    author_id = payload.get("author_id")
                    if author_id == client.state.my_player_id or payload.get("is_system"): return

                    text = payload.get("text", "").strip()
                    if text.isdigit():
                        target_id = client._resolve_id(text)
                        if target_id and target_id in client.state.players:
                            await asyncio.sleep(1) # Delay for realism
                            BotLogger.bt(f"Junior Werewolf selecting target from teammate chat: {text} -> {target_id}")
                            await client.junior_werewolf_select(target_id)
                            if not partner_id:
                                await client.wolf_vote(target_id)
                            else:
                                BotLogger.bt("Junior Werewolf is coupled, so keeping the vote for the partner.")
                            has_set_junior_target = True

                @client.on("cupid_lovers_revealed")
                async def on_lovers(payload):
                    nonlocal partner_id, partner_role, wolf_partner_id, has_messaged_partner_slot, has_junior_teammate, teammates_received
                    if partner_id: return
                    lover_ids = payload.get("lover_ids", [])
                    lover_roles = payload.get("lover_roles", [])
                    if client.state.my_player_id in lover_ids:
                        partners = [pid for pid in lover_ids if pid != client.state.my_player_id]
                        if partners:
                            partner_id = partners[0]
                            # Find partner's role
                            for pid, role in zip(lover_ids, lover_roles):
                                if pid == partner_id:
                                    partner_role = role
                                    break
                            
                            role = client.state.my_role
                            is_wolf = role and ("wolf" in role.lower() or "werewolf" in role.lower())
                            if is_wolf and client.state.phase == "night":
                                # Wait up to 1.5 seconds for werewolf teammates to be received
                                for _ in range(15):
                                    if teammates_received:
                                        break
                                    await asyncio.sleep(0.1)
                                    
                                is_junior = role and "junior-werewolf" in role.lower()
                                if not has_junior_teammate and not is_junior:
                                    has_junior_teammate = any(
                                        pid != client.state.my_player_id and p.known_role and "junior-werewolf" in p.known_role.lower()
                                        for pid, p in client.state.players.items()
                                    )
                                if has_junior_teammate:
                                    if not has_messaged_partner_slot:
                                        partner_player = client.state.players.get(partner_id)
                                        partner_number = partner_player.grid_idx + 1 if (partner_player and partner_player.grid_idx is not None) else None
                                        if partner_number:
                                            has_messaged_partner_slot = True
                                            BotLogger.bt(f"Lovers: Teammate is Junior WW! Canceling vote and posting partner slot {partner_number} in wolf chat.")
                                            await client._emit("game-werewolves-vote-remove", {})
                                            await asyncio.sleep(1)
                                            await client.send_wolf_message(str(partner_number))
                                else:
                                    avoid = partner_role and any(x in partner_role.lower() for x in ["gunner", "priest", "vigilante"])
                                    if not avoid:
                                        await asyncio.sleep(1)
                                        await client.wolf_vote(partner_id)
                                    else:
                                        BotLogger.bt(f"Lovers: Partner is {partner_role}, avoiding night kill!")
                        for pid, role in zip(lover_ids, lover_roles):
                            if pid != client.state.my_player_id and role:
                                if "wolf" in role.lower() or "werewolf" in role.lower():
                                    wolf_partner_id = pid

                @client.on("phase_change")
                async def on_phase(payload):
                    nonlocal has_said_me, partner_id, partner_role, wolf_partner_id, has_messaged_partner_slot, has_junior_teammate, teammates_received
                    phase = payload.get("phase")
                    stats.status = f"In Game: {phase}"
                    live.update(stats.generate_dashboard())
                    
                    role = client.state.my_role
                    is_wolf = role and ("wolf" in role.lower() or "werewolf" in role.lower())
                    if phase == "day-discussion" and not has_said_me and is_wolf:
                        await asyncio.sleep(2)
                        await client.send_message("me")
                        has_said_me = True
                    if phase == "night" and partner_id and is_wolf:
                        # Wait up to 1.5 seconds for werewolf teammates to be received
                        for _ in range(15):
                            if teammates_received:
                                break
                            await asyncio.sleep(0.1)
                            
                        is_junior = role and "junior-werewolf" in role.lower()
                        if not has_junior_teammate and not is_junior:
                            has_junior_teammate = any(
                                pid != client.state.my_player_id and p.known_role and "junior-werewolf" in p.known_role.lower()
                                for pid, p in client.state.players.items()
                            )
                        if has_junior_teammate:
                            if not has_messaged_partner_slot:
                                partner_player = client.state.players.get(partner_id)
                                partner_number = partner_player.grid_idx + 1 if (partner_player and partner_player.grid_idx is not None) else None
                                if partner_number:
                                    has_messaged_partner_slot = True
                                    BotLogger.bt(f"Lovers: Teammate is Junior WW! Canceling vote and posting partner slot {partner_number} in wolf chat.")
                                    await client._emit("game-werewolves-vote-remove", {})
                                    await asyncio.sleep(1)
                                    await client.send_wolf_message(str(partner_number))
                        else:
                            avoid = partner_role and any(x in partner_role.lower() for x in ["gunner", "priest", "vigilante"])
                            if not avoid:
                                await asyncio.sleep(3)
                                await client.wolf_vote(partner_id)
                            else:
                                BotLogger.bt(f"Lovers: Partner is {partner_role}, avoiding night kill!")

                    if phase == "day-voting":
                        if wolf_partner_id and not is_wolf:
                            await asyncio.sleep(2)
                            await client.vote(wolf_partner_id)
                        else:
                            await asyncio.sleep(6)
                            target_id, v_count = client.get_most_voted_player_id()
                            if target_id: await client.vote(target_id)

                # Background task to monitor for game-starting event within 15 seconds after joining
                async def check_lobby_start_timeout():
                    try:
                        await game_joined_event.wait()
                        await asyncio.wait_for(game_starting_event.wait(), timeout=15.0)
                    except asyncio.TimeoutError:
                        BotLogger.danger("Lobby Timeout: No game-starting event within 15 seconds of joining!")
                        nonlocal join_failed
                        join_failed = True
                        game_over_event.set()

                lobby_timeout_task = asyncio.create_task(check_lobby_start_timeout())
                connect_task = asyncio.create_task(client.connect())
                
                # Wait for the game to start or a join error to occur
                # If we don't even connect within 600 seconds, something is wrong
                try:
                    await asyncio.wait_for(game_over_event.wait(), timeout=600.0) # 10 min max per game
                    
                    # After game-over, wait a few seconds for the awards event to arrive if join was successful
                    if not join_failed:
                        try:
                            await asyncio.wait_for(awards_event.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                        
                except asyncio.TimeoutError:
                    stats.status = "Game Timeout"
                    live.update(stats.generate_dashboard())
                
                # 1. Fire and forget the disconnect to avoid blocking
                asyncio.create_task(client.disconnect())

                # 2. Cancel timeout and connect tasks immediately
                lobby_timeout_task.cancel()
                connect_task.cancel()

                # 3. Memory cleanup (runs fast enough to stay synchronous)
                stats.status = "Memory cleanup..."
                live.update(stats.generate_dashboard())
                import gc
                gc.collect()
                    
            except Exception as e:
                stats.status = "Retrying..."
                live.update(stats.generate_dashboard())
                await asyncio.sleep(10)
            finally:
                last_game_ended_time = time.time()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Wolvesville EXP Farmer")
    parser.add_argument("--strict", action="store_true", help="Only connect to the specific lobby with decorative characters")
    args = parser.parse_args()
    
    STRICT_MODE = args.strict
    if STRICT_MODE:
        BotLogger.info("🔒 Strict Lobby Filter Mode Enabled! Will only connect to the decorative name.")
        
    try:
        asyncio.run(farm())
    except KeyboardInterrupt:
        pass
