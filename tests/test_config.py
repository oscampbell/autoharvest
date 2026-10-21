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
        "HOURS_PER_ALERT": "0.5",
        "TIMEZONE": "Europe/London",
    }
    saved_path = save_config_to_env(updates, target_path=env_file)
    assert saved_path.exists()

    cfg = load_config(env_path=env_file)
    assert cfg.pagerduty_api_token == "token_123"
    assert cfg.harvest_access_token == "token_456"
    assert cfg.harvest_account_id == "987654"
    assert cfg.harvest_project_id == 111
    assert cfg.harvest_task_id == 222
    assert cfg.hours_per_alert == 0.5
    assert cfg.timezone == "Europe/London"
    assert cfg.get_tz() == ZoneInfo("Europe/London")
