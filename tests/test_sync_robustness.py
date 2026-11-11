from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from autoharvest.models import AcknowledgedIncident, HarvestTimeEntry, SyncPlan, SyncStatus
from autoharvest.sync import create_sync_plan, execute_sync_plan


class _RangeAwarePagerDuty:
    """Fake PagerDuty client that only returns alerts acknowledged inside the requested range."""

    def __init__(self, incidents):
        self.incidents = incidents

    def get_oncall_shifts(self, user_id, since, until):
        return []

    def get_acknowledged_incidents(self, user_id, since, until, **kwargs):
        return [inc for inc in self.incidents if since <= inc.acknowledged_at < until]


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


def test_windowing_does_not_depend_on_requested_range():
    """
    Alerts at 01:00, 01:54 and 02:30 with 1h windows: 01:00 opens a window, 01:54 falls
    inside it, so 02:30 opens the next one. Starting the range at 02:12 must give the
    same answer for 02:30, even though 01:00 is more than one window before the range.
    """
    tz = ZoneInfo("UTC")
    t1 = datetime(2026, 10, 15, 1, 0, tzinfo=tz)
    t2 = datetime(2026, 10, 15, 1, 54, tzinfo=tz)
    t3 = datetime(2026, 10, 15, 2, 30, tzinfo=tz)
    pd_client = _RangeAwarePagerDuty([_incident(1, t1), _incident(2, t2), _incident(3, t3)])
    harvest_mock = MagicMock()
    harvest_mock.get_time_entries.return_value = []

    def status_of_third(since):
        plan = create_sync_plan(
            pd_client=pd_client,
            harvest_client=harvest_mock,
            user_id="U1",
            since=since,
            until=datetime(2026, 10, 15, 4, 0, tzinfo=tz),
            project_id=1,
            task_id=2,
            hours_per_alert=1.0,
            tz=tz,
            filter_oncall_shifts=False,
        )
        return next(i.status for i in plan.items if i.incident.incident_id == "INC3")

    assert status_of_third(datetime(2026, 10, 15, 0, 0, tzinfo=tz)) == SyncStatus.PENDING_CREATION
    assert status_of_third(datetime(2026, 10, 15, 2, 12, tzinfo=tz)) == SyncStatus.PENDING_CREATION


def test_execute_sync_plan_flags_timer_left_running():
    from autoharvest.models import SyncItem

    harvest_mock = MagicMock()
    harvest_mock.format_time_for_harvest.side_effect = lambda dt: dt.strftime("%H:%M")
    harvest_mock.create_time_entry.return_value = HarvestTimeEntry(
        id=5, spent_date="2026-10-13", hours=1.0, notes="", project_id=50, task_id=60, is_running=True,
    )
    t = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)
    item = SyncItem(incident=_incident(7, t), hours=1.0, status=SyncStatus.PENDING_CREATION)
    plan = SyncPlan(since=t, until=t, items=[item], project_id=50, task_id=60)

    execute_sync_plan(harvest_mock, plan)

    assert item.status == SyncStatus.CREATED
    assert "still running" in item.error_message
