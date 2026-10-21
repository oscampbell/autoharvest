from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

from autoharvest.models import (
    AcknowledgedIncident,
    HarvestTimeEntry,
    SyncPlan,
    SyncStatus,
)
from autoharvest.sync import (
    create_sync_plan,
    execute_sync_plan,
    is_incident_already_logged,
    parse_datetime,
)


def test_parse_datetime():
    tz = ZoneInfo("Europe/London")
    dt1 = parse_datetime("2026-10-15", tz)
    assert dt1.year == 2026
    assert dt1.month == 10
    assert dt1.day == 15
    assert dt1.tzinfo == tz

    dt2 = parse_datetime("2026-10-15T14:30:00Z", tz)
    assert dt2.year == 2026
    assert dt2.minute == 30


def test_is_incident_already_logged():
    inc = AcknowledgedIncident(
        incident_id="PD123",
        incident_number=999,
        title="Test Inc",
        service_name="API",
        urgency="high",
        html_url="https://pd/123",
        acknowledged_at=datetime.now(timezone.utc),
        spent_date="2026-10-15",
        log_entry_id="L1",
    )

    # 1. Matches external_reference_id
    entry1 = HarvestTimeEntry(
        id=1,
        spent_date="2026-10-15",
        hours=1.0,
        notes="On call work",
        project_id=1,
        task_id=2,
        external_reference_id="PD123",
    )
    assert is_incident_already_logged(inc, [entry1]) == entry1

    # 2. Matches [PD-PD123] in notes
    entry2 = HarvestTimeEntry(
        id=2,
        spent_date="2026-10-15",
        hours=1.0,
        notes="Resolved high load [PD-PD123]",
        project_id=1,
        task_id=2,
    )
    assert is_incident_already_logged(inc, [entry2]) == entry2

    # 3. Matches #999 in notes with matching date
    entry3 = HarvestTimeEntry(
        id=3,
        spent_date="2026-10-15",
        hours=1.0,
        notes="Incident #999 database repair",
        project_id=1,
        task_id=2,
    )
    assert is_incident_already_logged(inc, [entry3]) == entry3

    # 4. Unrelated entry does not match
    entry_unrelated = HarvestTimeEntry(
        id=4,
        spent_date="2026-10-15",
        hours=1.0,
        notes="General bug fixing",
        project_id=1,
        task_id=2,
    )
    assert is_incident_already_logged(inc, [entry_unrelated]) is None


def test_create_sync_plan_dedup_and_calculation():
    pd_mock = MagicMock()
    harvest_mock = MagicMock()

    inc1 = AcknowledgedIncident(
        incident_id="PD1",
        incident_number=1,
        title="Alert 1",
        service_name="Web",
        urgency="high",
        html_url="https://pd/1",
        acknowledged_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
        spent_date="2026-10-12",
        log_entry_id="L1",
    )
    inc2 = AcknowledgedIncident(
        incident_id="PD2",
        incident_number=2,
        title="Alert 2",
        service_name="Web",
        urgency="high",
        html_url="https://pd/2",
        acknowledged_at=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
        spent_date="2026-10-13",
        log_entry_id="L2",
    )

    pd_mock.get_acknowledged_incidents.return_value = [inc1, inc2]

    # Harvest already has PD1 logged
    existing_entry = HarvestTimeEntry(
        id=101,
        spent_date="2026-10-12",
        hours=1.0,
        notes="PagerDuty Incident #1: Alert 1 [PD-PD1]",
        project_id=50,
        task_id=60,
        external_reference_id="PD1",
    )
    harvest_mock.get_time_entries.return_value = [existing_entry]

    since = datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)

    plan = create_sync_plan(
        pd_client=pd_mock,
        harvest_client=harvest_mock,
        user_id="U1",
        since=since,
        until=until,
        project_id=50,
        task_id=60,
        hours_per_alert=1.0,
    )

    assert len(plan.items) == 2
    assert len(plan.already_logged_items) == 1
    assert plan.already_logged_items[0].incident.incident_id == "PD1"
    assert plan.already_logged_items[0].harvest_entry_id == 101

    assert len(plan.pending_items) == 1
    assert plan.pending_items[0].incident.incident_id == "PD2"
    assert plan.pending_items[0].hours == 1.0

    assert plan.total_pending_hours == 1.0
    assert plan.total_logged_hours == 1.0


def test_sohonet_tableau_note_format():
    inc = AcknowledgedIncident(
        incident_id="PT4KHLK",
        incident_number=267540,
        title="SSL Certificate for www.sohonet.com due to expire soon",
        service_name="Webservers",
        urgency="high",
        html_url="https://company.pagerduty.com/incidents/PT4KHLK",
        acknowledged_at=datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc),
        spent_date="2026-10-15",
        log_entry_id="LOG1",
    )
    from autoharvest.models import SyncItem
    item = SyncItem(incident=inc, hours=1.0, status=SyncStatus.PENDING_CREATION)
    # Must match: PG#267540 - Webservers - SSL Certificate for www.sohonet.com due to expire soon
    expected_note = "PG#267540 - Webservers - SSL Certificate for www.sohonet.com due to expire soon"
    assert item.formatted_note == expected_note
    assert item.formatted_note.startswith("PG#267540")
    assert not item.formatted_note.startswith("PG# ")


def test_one_hour_windowing_algorithm():
    """
    Test user requirement:
    - each pagerduty alert creates 1 hour of bookable time
    - any alerts that happen within that hour are ignored
    - the next bookable hour starts with the next alert that happens after the end of the first bookable hour
    """
    pd_mock = MagicMock()
    harvest_mock = MagicMock()
    harvest_mock.get_time_entries.return_value = []

    tz = ZoneInfo("UTC")
    # Base: 2026-10-15 01:00:00
    base = datetime(2026, 10, 15, 1, 0, tzinfo=tz)

    # 6 incidents
    times = [
        base.replace(minute=10),  # 01:10 -> BOOKABLE (window: 01:10 - 02:10)
        base.replace(minute=25),  # 01:25 -> IGNORED (< 02:10)
        base.replace(minute=55),  # 01:55 -> IGNORED (< 02:10)
        base.replace(hour=2, minute=15),  # 02:15 -> BOOKABLE (window: 02:15 - 03:15)
        base.replace(hour=2, minute=45),  # 02:45 -> IGNORED (< 03:15)
        base.replace(hour=3, minute=20),  # 03:20 -> BOOKABLE (window: 03:20 - 04:20)
    ]

    incidents = [
        AcknowledgedIncident(
            incident_id=f"INC{i}",
            incident_number=100 + i,
            title=f"Incident {i}",
            service_name="Core",
            urgency="high",
            html_url=f"https://pd/{i}",
            acknowledged_at=t,
            spent_date=t.strftime("%Y-%m-%d"),
            log_entry_id=f"L{i}",
            incident_created_at=t,
        )
        for i, t in enumerate(times, start=1)
    ]
    pd_mock.get_acknowledged_incidents.return_value = incidents

    plan = create_sync_plan(
        pd_client=pd_mock,
        harvest_client=harvest_mock,
        user_id="U1",
        since=base,
        until=base.replace(hour=4),
        project_id=1,
        task_id=2,
        hours_per_alert=1.0,
        tz=tz,
    )

    assert len(plan.items) == 6

    # Items 1, 4, 6 should be pending creation (1.0 hr each)
    assert plan.items[0].status == SyncStatus.PENDING_CREATION
    assert plan.items[0].hours == 1.0
    assert plan.items[0].incident.incident_number == 101

    assert plan.items[1].status == SyncStatus.IGNORED_WINDOW
    assert plan.items[1].hours == 0.0
    assert plan.items[1].ignored_by_incident.incident_number == 101

    assert plan.items[2].status == SyncStatus.IGNORED_WINDOW
    assert plan.items[2].hours == 0.0
    assert plan.items[2].ignored_by_incident.incident_number == 101

    assert plan.items[3].status == SyncStatus.PENDING_CREATION
    assert plan.items[3].hours == 1.0
    assert plan.items[3].incident.incident_number == 104

    assert plan.items[4].status == SyncStatus.IGNORED_WINDOW
    assert plan.items[4].hours == 0.0
    assert plan.items[4].ignored_by_incident.incident_number == 104

    assert plan.items[5].status == SyncStatus.PENDING_CREATION
    assert plan.items[5].hours == 1.0
    assert plan.items[5].incident.incident_number == 106

    assert len(plan.pending_items) == 3
    assert len(plan.ignored_items) == 3
    assert plan.total_pending_hours == 3.0


def test_execute_sync_plan():
    harvest_mock = MagicMock()

    inc2 = AcknowledgedIncident(
        incident_id="PD2",
        incident_number=2,
        title="Alert 2",
        service_name="Web",
        urgency="high",
        html_url="https://pd/2",
        acknowledged_at=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
        spent_date="2026-10-13",
        log_entry_id="L2",
    )

    created_entry = HarvestTimeEntry(
        id=999,
        spent_date="2026-10-13",
        hours=1.0,
        notes="PG#2 - Web - Alert 2",
        project_id=50,
        task_id=60,
    )
    harvest_mock.create_time_entry.return_value = created_entry

    plan = SyncPlan(
        since=datetime.now(timezone.utc),
        until=datetime.now(timezone.utc),
        items=[],
        project_id=50,
        task_id=60,
        hours_per_alert=1.0,
    )
    from autoharvest.models import SyncItem
    item = SyncItem(incident=inc2, hours=1.0, status=SyncStatus.PENDING_CREATION)
    plan.items = [item]

    execute_sync_plan(harvest_mock, plan)

    harvest_mock.create_time_entry.assert_called_once_with(
        project_id=50,
        task_id=60,
        spent_date="2026-10-13",
        hours=1.0,
        notes="PG#2 - Web - Alert 2",
        external_reference={"id": "PD2", "permalink": "https://pd/2"},
    )
    assert item.status == SyncStatus.CREATED
    assert item.harvest_entry_id == 999
