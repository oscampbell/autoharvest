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
        filter_oncall_shifts=False,
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


def test_shift_filtering_only_books_during_oncall():
    """
    Test user requirement:
    - On call hours are 1am-8am
    - Working hours 1pm-5:30pm (alerts acked here are NOT bookable)
    - Alerts outside on-call shifts are ignored!
    """
    pd_mock = MagicMock()
    harvest_mock = MagicMock()
    harvest_mock.get_time_entries.return_value = []

    tz = ZoneInfo("UTC")
    # Shift: 01:00 to 08:00 on 2026-08-29
    shift_start = datetime(2026, 8, 29, 1, 0, tzinfo=tz)
    shift_end = datetime(2026, 8, 29, 8, 0, tzinfo=tz)
    from autoharvest.models import PagerDutyShift
    shift = PagerDutyShift(
        start=shift_start,
        end=shift_end,
        schedule_id="SCHED1",
        schedule_summary="1am-8am Night Shift",
        user_id="U1",
    )
    pd_mock.get_oncall_shifts.return_value = [shift]

    # Alert 1: 04:43 (Inside shift -> SHOULD BE BOOKED)
    inc_oncall = AcknowledgedIncident(
        incident_id="PD_ONCALL",
        incident_number=101,
        title="Night Alert",
        service_name="Webservers",
        urgency="high",
        html_url="https://pd/101",
        acknowledged_at=datetime(2026, 8, 29, 4, 43, tzinfo=tz),
        spent_date="2026-08-29",
        log_entry_id="L1",
        incident_created_at=datetime(2026, 8, 29, 4, 43, tzinfo=tz),
    )

    # Alert 2: 15:30 (Daytime 3:30pm -> NOT ON CALL -> MUST BE IGNORED)
    inc_daytime = AcknowledgedIncident(
        incident_id="PD_DAYTIME",
        incident_number=102,
        title="Daytime Alert",
        service_name="Webservers",
        urgency="high",
        html_url="https://pd/102",
        acknowledged_at=datetime(2026, 8, 29, 15, 30, tzinfo=tz),
        spent_date="2026-08-29",
        log_entry_id="L2",
        incident_created_at=datetime(2026, 8, 29, 15, 30, tzinfo=tz),
    )

    pd_mock.get_acknowledged_incidents.return_value = [inc_oncall, inc_daytime]

    # 1. By default, daytime alert is excluded from items to prevent table clutter
    plan = create_sync_plan(
        pd_client=pd_mock,
        harvest_client=harvest_mock,
        user_id="U1",
        since=datetime(2026, 8, 29, 0, 0, tzinfo=tz),
        until=datetime(2026, 8, 29, 23, 59, tzinfo=tz),
        project_id=1,
        task_id=2,
        filter_oncall_shifts=True,
        include_non_oncall=False,
    )

    assert len(plan.items) == 1
    assert plan.items[0].incident.incident_id == "PD_ONCALL"
    assert plan.items[0].status == SyncStatus.PENDING_CREATION
    assert plan.items[0].hours == 1.0
    assert plan.non_oncall_ignored_count == 1
    assert plan.total_pending_hours == 1.0

    # 2. With include_non_oncall=True, daytime alert is shown with status IGNORED_NOT_ONCALL and 0.0 hours
    plan_with_non_oncall = create_sync_plan(
        pd_client=pd_mock,
        harvest_client=harvest_mock,
        user_id="U1",
        since=datetime(2026, 8, 29, 0, 0, tzinfo=tz),
        until=datetime(2026, 8, 29, 23, 59, tzinfo=tz),
        project_id=1,
        task_id=2,
        filter_oncall_shifts=True,
        include_non_oncall=True,
    )

    assert len(plan_with_non_oncall.items) == 2
    assert plan_with_non_oncall.items[0].incident.incident_id == "PD_ONCALL"
    assert plan_with_non_oncall.items[0].status == SyncStatus.PENDING_CREATION

    assert plan_with_non_oncall.items[1].incident.incident_id == "PD_DAYTIME"
    assert plan_with_non_oncall.items[1].status == SyncStatus.IGNORED_NOT_ONCALL
    assert plan_with_non_oncall.items[1].hours == 0.0
    assert plan_with_non_oncall.total_pending_hours == 1.0


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
        filter_oncall_shifts=False,
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
        started_time="10:00",
        ended_time="11:00",
    )
    harvest_mock.create_time_entry.return_value = created_entry
    harvest_mock.format_time_for_harvest.side_effect = lambda dt: dt.strftime("%H:%M")

    plan = SyncPlan(
        since=datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc),
        until=datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc),
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
        started_time="10:00",
        ended_time="11:00",
        external_reference={"id": "PD2", "permalink": "https://pd/2"},
    )
    assert item.status == SyncStatus.CREATED
    assert item.harvest_entry_id == 999


def test_is_sociable_hours_weekends_and_weekdays():
    """
    Test Sociable Hours definition:
    - Sociable hours: 08:30 to 00:00 – Weekends & 17:30 to 00:00 Weekdays
    - Unsociable hours: All other times (00:00 to 08:30 weekends, 00:00 to 17:30 weekdays)
    """
    from autoharvest.sync import is_sociable_hours

    tz = ZoneInfo("UTC")

    # 1. Weekend: Saturday 2026-10-17
    # 00:00 -> Unsociable
    assert is_sociable_hours(datetime(2026, 10, 17, 0, 0, tzinfo=tz), tz) is False
    # 08:29 -> Unsociable
    assert is_sociable_hours(datetime(2026, 10, 17, 8, 29, 59, tzinfo=tz), tz) is False
    # 08:30 -> Sociable
    assert is_sociable_hours(datetime(2026, 10, 17, 8, 30, 0, tzinfo=tz), tz) is True
    # 14:00 -> Sociable
    assert is_sociable_hours(datetime(2026, 10, 17, 14, 0, 0, tzinfo=tz), tz) is True
    # 23:59 -> Sociable
    assert is_sociable_hours(datetime(2026, 10, 17, 23, 59, 59, tzinfo=tz), tz) is True

    # 2. Weekend: Sunday 2026-10-18
    # 03:00 -> Unsociable
    assert is_sociable_hours(datetime(2026, 10, 18, 3, 0, 0, tzinfo=tz), tz) is False
    # 08:30 -> Sociable
    assert is_sociable_hours(datetime(2026, 10, 18, 8, 30, 0, tzinfo=tz), tz) is True
    # 20:15 -> Sociable
    assert is_sociable_hours(datetime(2026, 10, 18, 20, 15, 0, tzinfo=tz), tz) is True

    # 3. Weekday: Monday 2026-10-19
    # 00:00 -> Unsociable
    assert is_sociable_hours(datetime(2026, 10, 19, 0, 0, tzinfo=tz), tz) is False
    # 04:30 (night shift) -> Unsociable
    assert is_sociable_hours(datetime(2026, 10, 19, 4, 30, tzinfo=tz), tz) is False
    # 17:29 -> Unsociable
    assert is_sociable_hours(datetime(2026, 10, 19, 17, 29, 59, tzinfo=tz), tz) is False
    # 17:30 -> Sociable
    assert is_sociable_hours(datetime(2026, 10, 19, 17, 30, 0, tzinfo=tz), tz) is True
    # 21:00 -> Sociable
    assert is_sociable_hours(datetime(2026, 10, 19, 21, 0, 0, tzinfo=tz), tz) is True

    # 4. Timezone conversion (e.g. Europe/London with BST offset +1h)
    # 2026-08-15 (Saturday in BST): 07:30 UTC is 08:30 BST -> Sociable!
    london_tz = ZoneInfo("Europe/London")
    dt_bst = datetime(2026, 8, 15, 7, 30, tzinfo=ZoneInfo("UTC"))
    assert is_sociable_hours(dt_bst, london_tz) is True


def test_sociable_hours_booking_half_hour_chunks():
    """
    Test requirement:
    - Hours within sociable hours at the weekend are booked in 0.5 hour chunks.
    - Alerts within that 0.5 hour (30m) are ignored.
    - Next alert after 30m starts the next window.
    """
    pd_mock = MagicMock()
    harvest_mock = MagicMock()
    harvest_mock.get_time_entries.return_value = []

    tz = ZoneInfo("Europe/London")
    # Saturday 2026-10-17 10:00 BST (Sociable)
    base = datetime(2026, 10, 17, 10, 0, tzinfo=tz)

    # 3 alerts:
    # Alert 1: 10:00 (Sociable) -> 0.5h chunk, window ends at 10:30
    # Alert 2: 10:15 -> Ignored (<30m since Alert 1)
    # Alert 3: 10:35 -> Bookable (after 10:30, starts new 0.5h window)
    incidents = [
        AcknowledgedIncident(
            incident_id="PD_SAT_1",
            incident_number=501,
            title="Database slow query",
            service_name="Database",
            urgency="high",
            html_url="https://pd/501",
            acknowledged_at=base,
            spent_date=base.strftime("%Y-%m-%d"),
            log_entry_id="L1",
            incident_created_at=base,
        ),
        AcknowledgedIncident(
            incident_id="PD_SAT_2",
            incident_number=502,
            title="Database connection pool",
            service_name="Database",
            urgency="high",
            html_url="https://pd/502",
            acknowledged_at=base.replace(minute=15),
            spent_date=base.strftime("%Y-%m-%d"),
            log_entry_id="L2",
            incident_created_at=base.replace(minute=15),
        ),
        AcknowledgedIncident(
            incident_id="PD_SAT_3",
            incident_number=503,
            title="High disk usage",
            service_name="Storage",
            urgency="high",
            html_url="https://pd/503",
            acknowledged_at=base.replace(minute=35),
            spent_date=base.strftime("%Y-%m-%d"),
            log_entry_id="L3",
            incident_created_at=base.replace(minute=35),
        ),
    ]
    pd_mock.get_acknowledged_incidents.return_value = incidents

    plan = create_sync_plan(
        pd_client=pd_mock,
        harvest_client=harvest_mock,
        user_id="U1",
        since=base,
        until=base.replace(hour=12),
        project_id=10,
        task_id=20,
        tz=tz,
        filter_oncall_shifts=False,
    )

    assert len(plan.items) == 3

    # Alert 1: 0.5 hours
    assert plan.items[0].incident.incident_number == 501
    assert plan.items[0].status == SyncStatus.PENDING_CREATION
    assert plan.items[0].hours == 0.5
    assert plan.items[0].is_sociable is True
    assert plan.items[0].window_hours == 0.5

    # Alert 2: Ignored
    assert plan.items[1].incident.incident_number == 502
    assert plan.items[1].status == SyncStatus.IGNORED_WINDOW
    assert plan.items[1].hours == 0.0
    assert plan.items[1].ignored_by_incident.incident_number == 501
    assert plan.items[1].window_hours == 0.5

    # Alert 3: 0.5 hours
    assert plan.items[2].incident.incident_number == 503
    assert plan.items[2].status == SyncStatus.PENDING_CREATION
    assert plan.items[2].hours == 0.5
    assert plan.items[2].is_sociable is True

    # Total pending hours = 0.5 + 0.5 = 1.0 hour
    assert plan.total_pending_hours == 1.0


def test_sociable_vs_unsociable_windowing_and_boundary():
    """
    Test alert sequence crossing unsociable/sociable boundary:
    - 08:10 Sat (Unsociable -> 1.0 hour chunk, window until 09:10)
    - 08:50 Sat (Inside window of 08:10 alert -> Ignored)
    - 09:15 Sat (After window, Sociable -> 0.5 hour chunk, window until 09:45)
    - 09:30 Sat (Inside window of 09:15 alert -> Ignored)
    """
    pd_mock = MagicMock()
    harvest_mock = MagicMock()
    harvest_mock.get_time_entries.return_value = []

    tz = ZoneInfo("UTC")
    sat_date = "2026-10-17"

    t1 = datetime(2026, 10, 17, 8, 10, tzinfo=tz)  # Unsociable (<08:30)
    t2 = datetime(2026, 10, 17, 8, 50, tzinfo=tz)  # Within 1h of t1
    t3 = datetime(2026, 10, 17, 9, 15, tzinfo=tz)  # Sociable (>09:10 and >=08:30)
    t4 = datetime(2026, 10, 17, 9, 30, tzinfo=tz)  # Within 30m of t3

    incidents = [
        AcknowledgedIncident(
            incident_id=f"INC_{i}",
            incident_number=600 + i,
            title=f"Incident {i}",
            service_name="Core",
            urgency="high",
            html_url=f"https://pd/{i}",
            acknowledged_at=t,
            spent_date=sat_date,
            log_entry_id=f"L{i}",
            incident_created_at=t,
        )
        for i, t in enumerate([t1, t2, t3, t4], start=1)
    ]
    pd_mock.get_acknowledged_incidents.return_value = incidents

    plan = create_sync_plan(
        pd_client=pd_mock,
        harvest_client=harvest_mock,
        user_id="U1",
        since=datetime(2026, 10, 17, 8, 0, tzinfo=tz),
        until=datetime(2026, 10, 17, 12, 0, tzinfo=tz),
        project_id=1,
        task_id=2,
        tz=tz,
        filter_oncall_shifts=False,
    )

    assert len(plan.items) == 4

    # 1. 08:10 -> Unsociable: 1.0 hour
    assert plan.items[0].incident.incident_number == 601
    assert plan.items[0].status == SyncStatus.PENDING_CREATION
    assert plan.items[0].hours == 1.0
    assert plan.items[0].is_sociable is False
    assert plan.items[0].window_hours == 1.0

    # 2. 08:50 -> Ignored by 601
    assert plan.items[1].incident.incident_number == 602
    assert plan.items[1].status == SyncStatus.IGNORED_WINDOW
    assert plan.items[1].ignored_by_incident.incident_number == 601

    # 3. 09:15 -> Sociable: 0.5 hours
    assert plan.items[2].incident.incident_number == 603
    assert plan.items[2].status == SyncStatus.PENDING_CREATION
    assert plan.items[2].hours == 0.5
    assert plan.items[2].is_sociable is True
    assert plan.items[2].window_hours == 0.5

    # 4. 09:30 -> Ignored by 603
    assert plan.items[3].incident.incident_number == 604
    assert plan.items[3].status == SyncStatus.IGNORED_WINDOW
    assert plan.items[3].ignored_by_incident.incident_number == 603

    assert plan.total_pending_hours == 1.5


def test_already_logged_requires_whole_incident_tag():
    from autoharvest.sync import is_incident_already_logged

    inc = AcknowledgedIncident(
        incident_id="PX1", incident_number=2675, title="t", service_name="s",
        urgency="high", html_url="u", acknowledged_at=datetime.now(timezone.utc),
        spent_date="2026-10-15", log_entry_id="L1",
    )
    longer = HarvestTimeEntry(
        id=1, spent_date="2026-10-15", hours=1.0,
        notes="PG#267540 - Webservers - SSL cert", project_id=1, task_id=2,
    )
    loose = HarvestTimeEntry(
        id=2, spent_date="2026-10-15", hours=1.0,
        notes="Follow-up on #26750", project_id=1, task_id=2,
    )
    assert is_incident_already_logged(inc, [longer, loose]) is None

    exact = HarvestTimeEntry(
        id=3, spent_date="2026-10-01", hours=1.0,
        notes="PG#2675 - Webservers - SSL cert", project_id=1, task_id=2,
    )
    assert is_incident_already_logged(inc, [longer, exact]) == exact


def test_parse_end_datetime_includes_whole_day():
    from zoneinfo import ZoneInfo
    from autoharvest.sync import parse_end_datetime

    tz = ZoneInfo("Europe/London")
    assert parse_end_datetime("2026-10-08", tz) == datetime(2026, 10, 9, 0, 0, tzinfo=tz)
    # Explicit times are left unchanged
    assert parse_end_datetime("2026-10-08T12:30:00", tz) == datetime(2026, 10, 8, 12, 30, tzinfo=tz)


def test_create_sync_plan_scopes_harvest_entries_to_current_user():
    pd_mock = MagicMock()
    pd_mock.get_oncall_shifts.return_value = []
    pd_mock.get_acknowledged_incidents.return_value = []
    harvest_mock = MagicMock()
    harvest_mock.get_current_user.return_value.id = 42
    harvest_mock.get_time_entries.return_value = []

    create_sync_plan(
        pd_client=pd_mock,
        harvest_client=harvest_mock,
        user_id="PUSER",
        since=datetime(2026, 10, 1, tzinfo=timezone.utc),
        until=datetime(2026, 10, 8, tzinfo=timezone.utc),
        project_id=1,
        task_id=2,
    )

    harvest_mock.get_time_entries.assert_called_once_with(
        from_date="2026-10-01", to_date="2026-10-08", user_id=42
    )


def test_execute_sync_plan_does_not_cross_midnight():
    from autoharvest.models import SyncItem

    harvest_mock = MagicMock()
    harvest_mock.create_time_entry.return_value = HarvestTimeEntry(
        id=1, spent_date="2026-10-13", hours=1.0, notes="", project_id=50, task_id=60,
    )
    harvest_mock.format_time_for_harvest.side_effect = lambda dt: dt.strftime("%H:%M")

    inc = AcknowledgedIncident(
        incident_id="PD9", incident_number=9, title="Late alert", service_name="Web",
        urgency="high", html_url="https://pd/9",
        acknowledged_at=datetime(2026, 10, 13, 23, 45, tzinfo=timezone.utc),
        spent_date="2026-10-13", log_entry_id="L9",
    )
    plan = SyncPlan(
        since=datetime(2026, 10, 13, tzinfo=timezone.utc),
        until=datetime(2026, 10, 14, tzinfo=timezone.utc),
        items=[SyncItem(incident=inc, hours=1.0, status=SyncStatus.PENDING_CREATION)],
        project_id=50,
        task_id=60,
    )

    execute_sync_plan(harvest_mock, plan)

    kwargs = harvest_mock.create_time_entry.call_args.kwargs
    assert kwargs["spent_date"] == "2026-10-13"
    assert kwargs["hours"] == 1.0
    assert kwargs["started_time"] == "22:59"
    assert kwargs["ended_time"] == "23:59"
