"""`autoharvest config show` / `config set`: view or change single settings without re-running setup."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from dotenv import dotenv_values
from rich.console import Console
from rich.table import Table

from autoharvest.config import save_config_to_env

console = Console()


def _parse_bool(value: str) -> str:
    v = value.strip().lower()
    if v in ("1", "true", "yes", "on", "y"):
        return "true"
    if v in ("0", "false", "no", "off", "n"):
        return "false"
    raise click.BadParameter(f"'{value}' is not true/false.")


def _parse_positive_hours(value: str) -> str:
    try:
        hours = float(value)
    except ValueError:
        raise click.BadParameter(f"'{value}' is not a number of hours.")
    if hours <= 0 or hours > 24:
        raise click.BadParameter("Hours must be greater than 0 and at most 24.")
    return str(hours)


def _parse_id(value: str) -> str:
    if not value.strip().isdigit():
        raise click.BadParameter(f"'{value}' is not a numeric ID.")
    return value.strip()


def _parse_timezone(value: str) -> str:
    try:
        ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, ValueError):
        raise click.BadParameter(f"Unknown timezone '{value}'. Use an IANA name such as Europe/London.")
    return value.strip()


@dataclass(frozen=True)
class Setting:
    env_key: str
    parse: Callable[[str], str]
    help: str


# Only settings listed here can be changed, so a typo can't create a key nothing reads.
# API tokens are deliberately absent: passing them on the command line would put them in
# shell history. Use `autoharvest setup`, which prompts for them with hidden input.
SETTINGS: dict[str, Setting] = {
    "align-to-clock": Setting("ALIGN_TO_CLOCK", _parse_bool, "Book alerts as the clock hour/half-hour they fall in (true/false)"),
    "sociable-hours": Setting("SOCIABLE_HOURS_PER_ALERT", _parse_positive_hours, "Hours booked per sociable alert"),
    "unsociable-hours": Setting("UNSOCIABLE_HOURS_PER_ALERT", _parse_positive_hours, "Hours booked per unsociable alert"),
    "project-id": Setting("HARVEST_PROJECT_ID", _parse_id, "Harvest project to log against"),
    "task-id": Setting("HARVEST_TASK_ID", _parse_id, "Harvest task to log against"),
    "timezone": Setting("TIMEZONE", _parse_timezone, "IANA timezone, e.g. Europe/London"),
}

SECRET_KEYS = {"PAGERDUTY_API_TOKEN", "HARVEST_ACCESS_TOKEN"}


def _env_path() -> Path:
    """The --config file if one was given, otherwise ./.env (the same place setup writes)."""
    root = click.get_current_context().find_root()
    config_path: Optional[str] = root.params.get("config_path")
    return Path(config_path) if config_path else Path.cwd() / ".env"


@click.group("config")
def config_group() -> None:
    """View or change individual settings in your .env file."""


@config_group.command("set")
@click.argument("name", type=click.Choice(sorted(SETTINGS), case_sensitive=False))
@click.argument("value")
def config_set(name: str, value: str) -> None:
    """Change one setting: autoharvest config set align-to-clock true"""
    setting = SETTINGS[name.lower()]
    new_value = setting.parse(value)
    path = _env_path()
    old_value = dotenv_values(path).get(setting.env_key) if path.exists() else None
    save_config_to_env({setting.env_key: new_value}, target_path=path)
    console.print(
        f"[green]✓ {name}:[/] {old_value if old_value not in (None, '') else '(not set)'} → [bold]{new_value}[/]"
        f"  [dim]({path.resolve()})[/dim]"
    )


@config_group.command("show")
def config_show() -> None:
    """Show the settings in your .env file (API tokens are hidden)."""
    path = _env_path()
    if not path.exists():
        console.print(f"[yellow]No config file at {path.resolve()}. Run `autoharvest setup` first.[/yellow]")
        return
    table = Table(title=str(path.resolve()), show_header=True)
    table.add_column("Setting")
    table.add_column("Value")
    for key, value in dotenv_values(path).items():
        if key in SECRET_KEYS and value:
            value = "•••• (hidden)"
        table.add_row(key, value or "")
    console.print(table)
