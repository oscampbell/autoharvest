from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values, find_dotenv, load_dotenv


@dataclass
class Config:
    pagerduty_api_token: str
    harvest_access_token: str
    harvest_account_id: str
    pagerduty_user_id: Optional[str] = None
    pagerduty_base_url: str = "https://api.pagerduty.com"
    harvest_project_id: Optional[int] = None
    harvest_task_id: Optional[int] = None
    harvest_user_agent: Optional[str] = None
    hours_per_alert: float = 1.0
    timezone: str = "UTC"

    def get_tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    def validate_pagerduty(self) -> list[str]:
        errors = []
        if not self.pagerduty_api_token:
            errors.append("PAGERDUTY_API_TOKEN is not set.")
        return errors

    def validate_harvest(self) -> list[str]:
        errors = []
        if not self.harvest_access_token:
            errors.append("HARVEST_ACCESS_TOKEN is not set.")
        if not self.harvest_account_id:
            errors.append("HARVEST_ACCOUNT_ID is not set.")
        return errors

    def validate_sync(self) -> list[str]:
        errors = self.validate_pagerduty() + self.validate_harvest()
        if not self.harvest_project_id:
            errors.append("HARVEST_PROJECT_ID is not set. Run `autoharvest setup` or set it in .env.")
        if not self.harvest_task_id:
            errors.append("HARVEST_TASK_ID is not set. Run `autoharvest setup` or set it in .env.")
        if self.hours_per_alert <= 0:
            errors.append("HOURS_PER_ALERT must be a positive number.")
        return errors


def get_default_timezone() -> str:
    """Detect local system timezone, falling back to UTC."""
    try:
        import time
        if time.tzname and time.tzname[0]:
            # Try to resolve from /etc/localtime or local name
            try:
                local_tz = Path("/etc/localtime").resolve()
                parts = local_tz.parts
                if "zoneinfo" in parts:
                    idx = parts.index("zoneinfo")
                    return "/".join(parts[idx + 1:])
            except Exception:
                pass
    except Exception:
        pass
    return "UTC"


def load_config(env_path: Optional[str | Path] = None) -> Config:
    """Load configuration from environment variables and/or .env file."""
    if env_path:
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        # Check current working directory, then default find_dotenv
        local_env = Path.cwd() / ".env"
        if local_env.exists():
            load_dotenv(dotenv_path=local_env)
        else:
            found = find_dotenv(usecwd=True)
            if found:
                load_dotenv(dotenv_path=found)

    # Parse integer values safely
    def parse_int(key: str) -> Optional[int]:
        val = os.getenv(key, "").strip()
        if val.isdigit():
            return int(val)
        return None

    def parse_float(key: str, default: float) -> float:
        val = os.getenv(key, "").strip()
        try:
            return float(val)
        except ValueError:
            return default

    tz = os.getenv("TIMEZONE", "").strip() or get_default_timezone()

    return Config(
        pagerduty_api_token=os.getenv("PAGERDUTY_API_TOKEN", "").strip(),
        pagerduty_user_id=os.getenv("PAGERDUTY_USER_ID", "").strip() or None,
        pagerduty_base_url=os.getenv("PAGERDUTY_BASE_URL", "https://api.pagerduty.com").rstrip("/"),
        harvest_access_token=os.getenv("HARVEST_ACCESS_TOKEN", "").strip(),
        harvest_account_id=os.getenv("HARVEST_ACCOUNT_ID", "").strip(),
        harvest_project_id=parse_int("HARVEST_PROJECT_ID"),
        harvest_task_id=parse_int("HARVEST_TASK_ID"),
        harvest_user_agent=os.getenv("HARVEST_USER_AGENT", "").strip() or None,
        hours_per_alert=parse_float("HOURS_PER_ALERT", 1.0),
        timezone=tz,
    )


def save_config_to_env(
    updates: dict[str, str],
    target_path: Optional[str | Path] = None,
) -> Path:
    """Update or create a .env file with given key-value pairs."""
    path = Path(target_path) if target_path else Path.cwd() / ".env"
    existing_vars = {}
    if path.exists():
        existing_vars = dotenv_values(path)

    merged = {**existing_vars, **updates}

    lines = [
        "# ==========================================",
        "# autoharvest configuration",
        "# Generated automatically via autoharvest setup",
        "# ==========================================",
        "",
    ]
    for k, v in merged.items():
        if v is not None:
            lines.append(f"{k}={v}")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
