from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from autoharvest.cli import cli
from autoharvest.models import HarvestProject, HarvestTask, HarvestUser, PagerDutyUser


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Sync PagerDuty acknowledged incidents" in result.output


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
