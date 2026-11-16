from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from autoharvest.config import load_config
from autoharvest.models import AcknowledgedIncident, HarvestTimeEntry, SyncStatus
from autoharvest.sync import clock_slot_start, create_sync_plan, execute_sync_plan

LONDON = ZoneInfo("Europe/London")


def _incident(n, t):
    return AcknowledgedIncident(
        incident_id=f"INC{n}",
        incident_number=n,
        title=f"Incident {n}",
        service_name="Core",
        urgency="high",
        html_url=f"https://pd/{n}",
        acknowledged_at=t,
        spent_date=t.strftime("%Y-%m-%d"),
        log_entry_id=f"L{n}",
        incident_created_at=t,
    )


def _plan(times, align_to_clock, since, until):
    pd_client = MagicMock()
    pd_client.get_acknowledged_incidents.return_value = [
        _incident(i, t) for i, t in enumerate(times, start=1)
    ]
    harvest = MagicMock()
    harvest.get_time_entries.return_value = []
    return create_sync_plan(
        pd_client=pd_client,
        harvest_client=harvest,
        user_id="U1",
        since=since,
        until=until,
        project_id=1,
        task_id=2,
        tz=LONDON,
        filter_oncall_shifts=False,
        align_to_clock=align_to_clock,
    )


def test_clock_slot_start():
    assert clock_slot_start(datetime(2026, 11, 8, 4, 37, tzinfo=LONDON), 1.0, LONDON) == datetime(2026, 11, 8, 4, 0, tzinfo=LONDON)
    assert clock_slot_start(datetime(2026, 11, 8, 17, 42, tzinfo=LONDON), 0.5, LONDON) == datetime(2026, 11, 8, 17, 30, tzinfo=LONDON)
    assert clock_slot_start(datetime(2026, 11, 8, 17, 29, tzinfo=LONDON), 0.5, LONDON) == datetime(2026, 11, 8, 17, 0, tzinfo=LONDON)


def test_align_to_clock_books_the_hour_the_alert_falls_in():
    # Sunday 8 Nov 2026: before 08:30 is unsociable (1h slots), after is sociable (0.5h slots).
    times = [
        datetime(2026, 11, 8, 4, 37, tzinfo=LONDON),   # -> 04:00-05:00
        datetime(2026, 11, 8, 4, 55, tzinfo=LONDON),   # same hour -> ignored
        datetime(2026, 11, 8, 5, 2, tzinfo=LONDON),    # -> 05:00-06:00 (only 25 min after 04:37)
        datetime(2026, 11, 8, 19, 44, tzinfo=LONDON),  # sociable -> 19:30-20:00
    ]
    plan = _plan(times, True, datetime(2026, 11, 8, 0, 0, tzinfo=LONDON), datetime(2026, 11, 9, 0, 0, tzinfo=LONDON))

    statuses = [i.status for i in plan.items]
    assert statuses == [
        SyncStatus.PENDING_CREATION,
        SyncStatus.IGNORED_WINDOW,
        SyncStatus.PENDING_CREATION,
        SyncStatus.PENDING_CREATION,
    ]
    booked = [(i.booked_start.strftime("%H:%M"), i.hours) for i in plan.pending_items]
    assert booked == [("04:00", 1.0), ("05:00", 1.0), ("19:30", 0.5)]


def test_default_mode_is_unchanged():
    times = [
        datetime(2026, 11, 8, 4, 37, tzinfo=LONDON),
        datetime(2026, 11, 8, 5, 2, tzinfo=LONDON),  # within 1h of 04:37 -> ignored
    ]
    plan = _plan(times, False, datetime(2026, 11, 8, 0, 0, tzinfo=LONDON), datetime(2026, 11, 9, 0, 0, tzinfo=LONDON))
    assert [i.status for i in plan.items] == [SyncStatus.PENDING_CREATION, SyncStatus.IGNORED_WINDOW]
    assert plan.items[0].booked_start is None


def test_execute_uses_the_clock_aligned_start():
    plan = _plan(
        [datetime(2026, 11, 8, 4, 37, tzinfo=LONDON)],
        True,
        datetime(2026, 11, 8, 0, 0, tzinfo=LONDON),
        datetime(2026, 11, 9, 0, 0, tzinfo=LONDON),
    )
    harvest = MagicMock()
    harvest.format_time_for_harvest.side_effect = lambda dt: dt.strftime("%H:%M")
    harvest.create_time_entry.return_value = HarvestTimeEntry(
        id=1, spent_date="2026-11-08", hours=1.0, notes="", project_id=1, task_id=2
    )

    execute_sync_plan(harvest, plan, tz=LONDON)

    kwargs = harvest.create_time_entry.call_args.kwargs
    assert (kwargs["started_time"], kwargs["ended_time"], kwargs["spent_date"]) == ("04:00", "05:00", "2026-11-08")


def test_align_to_clock_config(tmp_path, monkeypatch):
    monkeypatch.delenv("ALIGN_TO_CLOCK", raising=False)
    env = tmp_path / ".env"
    env.write_text("ALIGN_TO_CLOCK=true\n")
    assert load_config(env).align_to_clock is True
    monkeypatch.delenv("ALIGN_TO_CLOCK", raising=False)
    env.write_text("PAGERDUTY_API_TOKEN=x\n")
    assert load_config(env).align_to_clock is False


def test_cli_flag_and_config_default(tmp_path, monkeypatch):
    from unittest.mock import patch
    from click.testing import CliRunner
    from autoharvest.cli import cli
    from autoharvest.models import PagerDutyUser

    monkeypatch.delenv("ALIGN_TO_CLOCK", raising=False)
    times = [datetime(2026, 11, 8, 4, 37, tzinfo=LONDON)]
    env = tmp_path / ".env"
    base = "PAGERDUTY_API_TOKEN=t\nHARVEST_ACCESS_TOKEN=t\nHARVEST_ACCOUNT_ID=1\nHARVEST_PROJECT_ID=1\nHARVEST_TASK_ID=2\nTIMEZONE=Europe/London\n"

    def run(env_text, *flags):
        monkeypatch.delenv("ALIGN_TO_CLOCK", raising=False)
        env.write_text(env_text)
        with patch("autoharvest.cli.PagerDutyClient") as pd_cls, patch("autoharvest.cli.HarvestClient") as h_cls:
            pd = pd_cls.return_value
            pd.get_current_user.return_value = PagerDutyUser(id="U1", name="Davy", email="d@x", time_zone="UTC")
            pd.get_oncall_shifts.return_value = []
            pd.get_acknowledged_incidents.return_value = [_incident(1, times[0])]
            h_cls.return_value.get_time_entries.return_value = []
            return CliRunner().invoke(
                cli,
                ["--config", str(env), "sync", "--since", "2026-11-08", "--until", "2026-11-08T23:59",
                 "--all-alerts", "--dry-run", *flags],
            )

    aligned = run(base, "--align-to-clock")
    assert aligned.exit_code == 0, aligned.output
    assert "→ 04:00–05:00" in aligned.output

    from_config = run(base + "ALIGN_TO_CLOCK=true\n")
    assert "→ 04:00–05:00" in from_config.output

    overridden = run(base + "ALIGN_TO_CLOCK=true\n", "--from-alert-time")
    assert "→ 04:00" not in overridden.output
