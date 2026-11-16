from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from dotenv import dotenv_values

from autoharvest.cli import cli
from autoharvest.config import load_config
from autoharvest.models import HarvestUser, PagerDutyUser


def _env(tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(text)
    return env


def test_config_set_updates_one_setting_and_keeps_the_rest(tmp_path, monkeypatch):
    monkeypatch.delenv("ALIGN_TO_CLOCK", raising=False)
    env = _env(tmp_path, "HARVEST_ACCESS_TOKEN=secret\nTIMEZONE=Europe/London\n")

    result = CliRunner().invoke(cli, ["--config", str(env), "config", "set", "align-to-clock", "yes"])

    assert result.exit_code == 0, result.output
    assert "(not set) → true" in result.output
    values = dotenv_values(env)
    assert values["ALIGN_TO_CLOCK"] == "true"
    assert values["HARVEST_ACCESS_TOKEN"] == "secret"
    assert values["TIMEZONE"] == "Europe/London"
    monkeypatch.delenv("ALIGN_TO_CLOCK", raising=False)
    assert load_config(env).align_to_clock is True


def test_config_set_rejects_bad_values_without_writing(tmp_path):
    env = _env(tmp_path, "TIMEZONE=Europe/London\n")
    runner = CliRunner()

    for args in (
        ["align-to-clock", "maybe"],
        ["timezone", "Europe/Lndon"],
        ["sociable-hours", "0"],
        ["task-id", "abc"],
    ):
        result = runner.invoke(cli, ["--config", str(env), "config", "set", *args])
        assert result.exit_code == 2, (args, result.output)

    assert env.read_text() == "TIMEZONE=Europe/London\n"


def test_config_set_refuses_unknown_settings_and_tokens(tmp_path):
    env = _env(tmp_path, "")
    runner = CliRunner()
    for name in ("align-to-clok", "harvest-access-token"):
        result = runner.invoke(cli, ["--config", str(env), "config", "set", name, "x"])
        assert result.exit_code == 2
    assert env.read_text() == ""


def test_config_show_hides_tokens(tmp_path):
    env = _env(tmp_path, "PAGERDUTY_API_TOKEN=pd-secret\nHARVEST_ACCESS_TOKEN=h-secret\nALIGN_TO_CLOCK=true\n")
    result = CliRunner(env={"COLUMNS": "200"}).invoke(cli, ["--config", str(env), "config", "show"])
    assert result.exit_code == 0, result.output
    assert "pd-secret" not in result.output and "h-secret" not in result.output
    assert "hidden" in result.output
    assert "ALIGN_TO_CLOCK" in result.output


@patch("autoharvest.cli.HarvestClient")
@patch("autoharvest.cli.PagerDutyClient")
def test_setup_asks_about_clock_alignment(mock_pd_cls, mock_harvest_cls, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("ALIGN_TO_CLOCK", "TIMEZONE", "PAGERDUTY_API_TOKEN", "HARVEST_ACCESS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    mock_pd_cls.return_value.get_current_user.return_value = PagerDutyUser(
        id="P1", name="Davy", email="d@example.com", time_zone="UTC"
    )
    mock_harvest_cls.return_value.get_current_user.return_value = HarvestUser(
        id=9, first_name="Davy", last_name="Tan", email="d@example.com"
    )
    mock_harvest_cls.return_value.get_project_assignments.return_value = []

    # pd token, harvest token, account id, project, task, sociable, unsociable, timezone, salary, align
    answers = "pdtok\nhtok\n123\n1\n2\n0.5\n1.0\nEurope/London\n\ny\n"
    result = CliRunner().invoke(cli, ["setup"], input=answers)

    assert result.exit_code == 0, result.output
    assert "clock hour/half-hour" in result.output
    saved = dotenv_values(tmp_path / ".env")
    assert saved["ALIGN_TO_CLOCK"] == "true"
