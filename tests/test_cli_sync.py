from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from autoharvest.cli import cli
from autoharvest.models import PagerDutyUser


@patch("autoharvest.cli.execute_sync_plan")
@patch("autoharvest.cli.create_sync_plan")
@patch("autoharvest.cli.PagerDutyClient")
@patch("autoharvest.cli.HarvestClient")
def test_cli_sync_warns_about_running_timer_and_company_settings(
    mock_harvest_cls, mock_pd_cls, mock_create_plan, mock_execute, tmp_path
):
    from datetime import datetime, timezone
    from autoharvest.models import AcknowledgedIncident, SyncItem, SyncPlan, SyncStatus

    mock_pd = MagicMock()
    mock_pd.get_current_user.return_value = PagerDutyUser(
        id="P1", name="Oliver Campbell", email="oliver@test.com", time_zone="UTC"
    )
    mock_pd_cls.return_value = mock_pd
    mock_harvest = MagicMock()
    mock_harvest.company_config_error = "500 Server Error"
    mock_harvest_cls.return_value = mock_harvest

    t = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
    incident = AcknowledgedIncident(
        incident_id="PD1", incident_number=42, title="Disk full", service_name="Core",
        urgency="high", html_url="https://pd/1", acknowledged_at=t, spent_date="2026-10-13",
        log_entry_id="L1",
    )
    item = SyncItem(incident=incident, hours=1.0, status=SyncStatus.PENDING_CREATION)
    mock_create_plan.return_value = SyncPlan(since=t, until=t, items=[item], project_id=1, task_id=2)

    def execute(harvest_client, plan, tz=None):
        item.status = SyncStatus.CREATED
        item.error_message = "Created, but the Harvest timer is still running. Stop it in Harvest."
        return plan.items

    mock_execute.side_effect = execute

    env_file = tmp_path / ".env"
    env_file.write_text(
        "PAGERDUTY_API_TOKEN=tok\nHARVEST_ACCESS_TOKEN=tok\nHARVEST_ACCOUNT_ID=123\n"
        "HARVEST_PROJECT_ID=1\nHARVEST_TASK_ID=2\n"
    )
    result = CliRunner().invoke(cli, ["--config", str(env_file), "sync", "--all-alerts", "--apply"])

    assert result.exit_code == 0, result.output
    assert "Couldn't read your Harvest company settings" in result.output
    assert "left with a running timer" in result.output
    assert "Incident #42" in result.output
    assert "failed to create" not in result.output
    assert "Successfully logged" not in result.output
