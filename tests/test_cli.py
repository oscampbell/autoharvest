from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from autoharvest.cli import cli
from autoharvest.models import HarvestProject, HarvestTask, HarvestUser, PagerDutyUser


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Sync PagerDuty acknowledged incidents" in result.output
    assert "setup" in result.output


def test_cli_test_missing_config(tmp_path):
    runner = CliRunner()
    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    result = runner.invoke(cli, ["--config", str(empty_env), "test"])
    assert result.exit_code != 0
    assert "Configuration errors found" in result.output


@patch("autoharvest.cli.PagerDutyClient")
@patch("autoharvest.cli.HarvestClient")
def test_cli_test_success(mock_harvest_cls, mock_pd_cls, tmp_path):
    mock_pd = MagicMock()
    mock_pd.get_current_user.return_value = PagerDutyUser(
        id="P1", name="Oliver Campbell", email="oliver@test.com", time_zone="UTC"
    )
    mock_pd_cls.return_value = mock_pd

    mock_harvest = MagicMock()
    mock_harvest.get_current_user.return_value = HarvestUser(
        id=9, first_name="Oliver", last_name="Campbell", email="oliver@test.com"
    )
    mock_harvest_cls.return_value = mock_harvest

    runner = CliRunner()
    env_file = tmp_path / ".env"
    env_file.write_text("PAGERDUTY_API_TOKEN=tok\nHARVEST_ACCESS_TOKEN=tok\nHARVEST_ACCOUNT_ID=123\n")

    result = runner.invoke(cli, ["--config", str(env_file), "test"])
    assert result.exit_code == 0
    assert "Connections Successful" in result.output
    assert "Oliver Campbell" in result.output


@patch("autoharvest.cli.HarvestClient")
def test_cli_list_projects(mock_harvest_cls, tmp_path):
    mock_harvest = MagicMock()
    mock_harvest.get_project_assignments.return_value = [
        HarvestProject(
            id=10,
            name="SRE Operations",
            tasks=[HarvestTask(id=20, name="On-Call Support")],
        )
    ]
    mock_harvest_cls.return_value = mock_harvest

    runner = CliRunner()
    env_file = tmp_path / ".env"
    env_file.write_text("PAGERDUTY_API_TOKEN=tok\nHARVEST_ACCESS_TOKEN=tok\nHARVEST_ACCOUNT_ID=123\n")

    result = runner.invoke(cli, ["--config", str(env_file), "list-projects"])
    assert result.exit_code == 0
    assert "SRE Operations" in result.output
    assert "On-Call Support" in result.output


@patch("autoharvest.cli.HarvestClient")
def test_cli_overtime(mock_harvest_cls, tmp_path: Path):
    from autoharvest.models import HarvestTimeEntry

    mock_harvest = MagicMock()
    mock_harvest.get_time_entries.return_value = [
        HarvestTimeEntry(
            id=101,
            spent_date="2026-10-15",
            hours=2.0,
            notes="PG#101 - Alert",
            project_id=1,
            project_name="UK Support",
            task_id=10,
            task_name="1st On call - Active",
        ),
        HarvestTimeEntry(
            id=102,
            spent_date="2026-10-16",
            hours=15.0,
            notes="",
            project_id=2,
            project_name="xOn Call InActive",
            task_id=20,
            task_name="1st On Call - Inactive",
        ),
    ]
    mock_harvest_cls.return_value = mock_harvest

    runner = CliRunner()
    env_file = tmp_path / ".env"
    env_file.write_text("PAGERDUTY_API_TOKEN=tok\nHARVEST_ACCESS_TOKEN=tok\nHARVEST_ACCOUNT_ID=123\n")

    # Test with explicit --month (defaults to payday cycle)
    result = runner.invoke(cli, ["--config", str(env_file), "overtime", "--month", "2026-10"])
    assert result.exit_code == 0
    assert "End of October 2026 Payday" in result.output
    assert "1st On Call - Active" in result.output
    assert "1st On Call - Inactive" in result.output
    assert "17.00 hrs" in result.output
    assert "£150.00" in result.output

    # Test default overtime (next coming payday)
    result_default = runner.invoke(cli, ["--config", str(env_file), "overtime"])
    assert result_default.exit_code == 0
    assert "Payday" in result_default.output

    # Test --last-payday flag
    result_last = runner.invoke(cli, ["--config", str(env_file), "overtime", "--last-payday"])
    assert result_last.exit_code == 0
    assert "Payday" in result_last.output

    # Test --calendar mode
    result_cal = runner.invoke(cli, ["--config", str(env_file), "overtime", "-c", "-m", "2026-10"])
    assert result_cal.exit_code == 0
    assert "Calendar Month: October 2026" in result_cal.output

    # Test --pay when no salary is configured
    result_pay_nosal = runner.invoke(cli, ["--config", str(env_file), "overtime", "--pay"])
    assert result_pay_nosal.exit_code == 0
    assert "To view estimated take-home pay, specify your annual salary" in result_pay_nosal.output

    # Test --salary option overrides/provides salary
    result_salary = runner.invoke(cli, ["--config", str(env_file), "overtime", "--salary", "48000"])
    assert result_salary.exit_code == 0
    assert "ESTIMATED TAKE-HOME" in result_salary.output
    assert "Estimated Monthly Pay Breakdown" in result_salary.output

    # Test --pay when salary is in .env
    env_file_with_sal = tmp_path / ".env.sal"
    env_file_with_sal.write_text("PAGERDUTY_API_TOKEN=tok\nHARVEST_ACCESS_TOKEN=tok\nHARVEST_ACCOUNT_ID=123\nANNUAL_SALARY=54000\n")
    result_pay_env = runner.invoke(cli, ["--config", str(env_file_with_sal), "overtime", "--pay"])
    assert result_pay_env.exit_code == 0
    assert "ESTIMATED TAKE-HOME" in result_pay_env.output
    assert "Annual Base Salary: £54,000.00" in result_pay_env.output


def test_cli_invalid_timezone_shows_clear_error(tmp_path):
    env = tmp_path / "custom.env"
    env.write_text("HARVEST_ACCESS_TOKEN=h\nHARVEST_ACCOUNT_ID=1\nTIMEZONE=Europe/Londn\n")
    result = CliRunner().invoke(cli, ["--config", str(env), "overtime"])
    assert result.exit_code != 0
    assert "Unknown TIMEZONE" in result.output


@patch("autoharvest.cli.PagerDutyClient")
@patch("autoharvest.cli.HarvestClient")
def test_cli_setup_writes_to_config_path(mock_harvest_cls, mock_pd_cls, tmp_path, monkeypatch):
    mock_pd = MagicMock()
    mock_pd.get_current_user.return_value = PagerDutyUser(
        id="P1", name="Test User", email="t@test.com", time_zone="UTC"
    )
    mock_pd_cls.return_value = mock_pd
    mock_harvest = MagicMock()
    mock_harvest.get_current_user.return_value = HarvestUser(
        id=7, first_name="Test", last_name="User", email="t@test.com"
    )
    mock_harvest.get_project_assignments.return_value = [
        HarvestProject(id=100, name="On Call", tasks=[HarvestTask(id=200, name="Active On-Call")])
    ]
    mock_harvest_cls.return_value = mock_harvest

    custom_env = tmp_path / "custom.env"
    custom_env.write_text("TIMEZONE=UTC\n")
    monkeypatch.chdir(tmp_path)

    # Accept every prompt's default; tokens are typed in.
    answers = iter(["pd_tok", "h_tok", "12345"])
    def fake_prompt_ask(*args, **kwargs):
        if kwargs.get("password") or "Account ID" in str(args[0] if args else ""):
            return next(answers)
        return str(kwargs.get("default", ""))
    monkeypatch.setattr("autoharvest.cli.Prompt.ask", fake_prompt_ask)
    monkeypatch.setattr("autoharvest.cli.FloatPrompt.ask", lambda *a, **k: k.get("default", 1.0))
    monkeypatch.setattr("autoharvest.cli.Confirm.ask", lambda *a, **k: k.get("default", False))

    result = CliRunner().invoke(cli, ["--config", str(custom_env), "setup"])

    assert result.exit_code == 0, result.output
    assert "PAGERDUTY_API_TOKEN=pd_tok" in custom_env.read_text()
    assert not (tmp_path / ".env").exists()
