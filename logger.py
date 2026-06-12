from rich.console import Console
from rich.theme import Theme
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from datetime import datetime
import os

custom_theme = Theme({
    "info": "dim cyan",
    "warning": "bold yellow",
    "danger": "bold red",
    "success": "bold green",
    "bt": "bold blue",
    "ext": "bold magenta",
    "system": "bold white on blue",
    "role": "bold underline white",
    "phase": "bold italic yellow",
})

console = Console(theme=custom_theme)
LOG_BUFFER = []
MAX_LOGS = 10

class BotLogger:
    capture_mode = False

    @classmethod
    def _log(cls, msg: str):
        if cls.capture_mode:
            LOG_BUFFER.append(msg)
            if len(LOG_BUFFER) > MAX_LOGS:
                LOG_BUFFER.pop(0)
        else:
            console.print(msg)

    @classmethod
    def info(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [info]{msg}[/info]")

    @classmethod
    def success(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [success]✔ {msg}[/success]")

    @classmethod
    def warning(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [warning]⚠ {msg}[/warning]")

    @classmethod
    def danger(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [danger]✖ {msg}[/danger]")

    @classmethod
    def bt(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [bt]󱐋 BT[/bt] {msg}")

    @classmethod
    def belief(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [cyan]󱐋 BELIEF[/cyan] {msg}")

    @classmethod
    def client(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [dim white]󱐋 CLIENT[/dim white] {msg}")

    @classmethod
    def extractor(cls, msg: str):
        cls._log(f"[{datetime.now().strftime('%H:%M:%S')}] [ext]󱜙 EXT[/ext] {msg}")

    @classmethod
    def system(cls, msg: str):
        if cls.capture_mode:
            cls._log(f"[bold white on blue] SYSTEM [/bold white on blue] {msg}")
        else:
            console.print(Panel(Text(msg, style="bold white"), style="system", expand=False))

    @staticmethod
    def game_status(phase, role, day, alive_count, state=None):
        table = Table(title="Game Status", show_header=False, box=None)
        table.add_row("Phase:", f"[phase]{phase}[/phase]")
        table.add_row("Role:", f"[role]{role}[/role]")
        table.add_row("Day:", str(day))
        table.add_row("Alive:", str(alive_count))

        if state:
            # Role-specific data
            role_lower = role.lower().replace(" ", "-") if role else ""
            if "witch" in role_lower:
                p = state.get("witch_potions", {})
                table.add_row("Potions:", f"Kill: {p.get('kill', 0)}, Protect: {p.get('protect', 0)}")
            elif "priest" in role_lower:
                table.add_row("Water:", str(state.get("priest_water_count", 0)))
            elif "harlot" in role_lower or "red-lady" in role_lower:
                visits = state.get("harlot_visits", [])
                targets = state.get("harlot_announced_targets", [])
                if visits: table.add_row("Visits:", ", ".join(map(str, visits)))
                if targets: table.add_row("Targets:", ", ".join(map(str, targets)))
            elif "junior-werewolf" in role_lower:
                target_id = state.get("junior_target_id")
                if target_id: table.add_row("Target ID:", str(target_id))

        console.print(Panel(table, border_style="blue"))
