from pathlib import Path
from zoneinfo import ZoneInfo
from autoharvest.config import Config, load_config, save_config_to_env


def test_config_validation():
    cfg = Config(
        pagerduty_api_token="",
        harvest_access_token="",
        harvest_account_id="",
    )
    assert len(cfg.validate_pagerduty()) == 1
    assert len(cfg.validate_harvest()) == 2
    assert len(cfg.validate_sync()) >= 4


def test_config_valid():
    cfg = Config(
        pagerduty_api_token="pd_test",
        harvest_access_token="h_test",
        harvest_account_id="12345",
        harvest_project_id=100,
        harvest_task_id=200,
        hours_per_alert=1.0,
        timezone="UTC",
    )
    assert cfg.validate_pagerduty() == []
    assert cfg.validate_harvest() == []
    assert cfg.validate_sync() == []
    assert cfg.get_tz() == ZoneInfo("UTC")


def test_save_and_load_config(tmp_path: Path):
    env_file = tmp_path / ".env"
    updates = {
        "PAGERDUTY_API_TOKEN": "token_123",
        "HARVEST_ACCESS_TOKEN": "token_456",
        "HARVEST_ACCOUNT_ID": "987654",
        "HARVEST_PROJECT_ID": "111",
        "HARVEST_TASK_ID": "222",
        "HOURS_PER_ALERT": "1.0",
        "SOCIABLE_HOURS_PER_ALERT": "0.5",
        "UNSOCIABLE_HOURS_PER_ALERT": "1.0",
        "TIMEZONE": "Europe/London",
        "ANNUAL_SALARY": "52000.00",
    }
    saved_path = save_config_to_env(updates, target_path=env_file)
    assert saved_path.exists()

    cfg = load_config(env_path=env_file)
    assert cfg.pagerduty_api_token == "token_123"
    assert cfg.harvest_access_token == "token_456"
    assert cfg.harvest_account_id == "987654"
    assert cfg.harvest_project_id == 111
    assert cfg.harvest_task_id == 222
    assert cfg.hours_per_alert == 1.0
    assert cfg.sociable_hours_per_alert == 0.5
    assert cfg.unsociable_hours_per_alert == 1.0
    assert cfg.timezone == "Europe/London"
    assert cfg.annual_salary == 52000.0
    assert cfg.get_tz() == ZoneInfo("Europe/London")


def test_invalid_timezone_raises_instead_of_falling_back_to_utc():
    import pytest

    cfg = Config(
        pagerduty_api_token="pd",
        harvest_access_token="h",
        harvest_account_id="1",
        timezone="Europe/Londn",
    )
    with pytest.raises(ValueError, match="Europe/Londn"):
        cfg.get_tz()


def test_saved_env_is_owner_only(tmp_path: Path):
    import os
    import stat

    path = save_config_to_env({"PAGERDUTY_API_TOKEN": "secret"}, target_path=tmp_path / ".env")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
