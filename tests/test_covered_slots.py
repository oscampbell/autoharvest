from datetime import datetime, time
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import responses
from click.testing import CliRunner

from autoharvest.harvest import HarvestClient
from autoharvest.models import AcknowledgedIncident, HarvestTimeEntry, PagerDutyUser, SyncStatus
from autoharvest.sync import (
    busy_intervals,
    create_sync_plan,
    execute_sync_plan,
    free_part_of_slot,
    is_incident_already_logged,
    parse_harvest_clock,
)

LONDON = ZoneInfo("Europe/London")
PROJECT, TASK = 31492143, 1068026


def at(day, hh, mm):
    return datetime(2026, 11, day, hh, mm, tzinfo=LONDON)


def incident(n, t):
    return AcknowledgedIncident(
        incident_id=f"P{n}", incident_number=n, title=f"Alert {n}", service_name="Core",
        urgency="high", html_url=f"https://pd/{n}", acknowledged_at=t,
        spent_date=t.strftime("%Y-%m-%d"), log_entry_id=f"L{n}", incident_created_at=t,
    )


def entry(entry_id, day, start, end, notes, task_id=TASK, project_id=PROJECT):
    return HarvestTimeEntry(
        id=entry_id, spent_date=f"2026-11-{day:02d}", hours=0.0, notes=notes,
        project_id=project_id, task_id=task_id, started_time=start, ended_time=end,
    )


def plan_for(alerts, existing, day, skip_covered=True, align=True):
    pd = MagicMock()
    pd.get_acknowledged_incidents.return_value = alerts
    pd.get_oncall_shifts.return_value = []
    harvest = MagicMock()
    harvest.get_time_entries.return_value = existing
    return create_sync_plan(
        pd, harvest, "U1", at(day, 0, 0), at(day + 1, 0, 0) if day < 30 else at(day, 23, 59),
        PROJECT, TASK, sociable_hours=0.5, unsociable_hours=1.0, tz=LONDON,
        filter_oncall_shifts=False, align_to_clock=align, skip_covered=skip_covered,
    )


def by_number(plan):
    return {i.incident.incident_number: i for i in plan.items}


def test_parse_harvest_clock_handles_both_formats():
    assert parse_harvest_clock("13:30") == time(13, 30)
    assert parse_harvest_clock("1:30") == time(1, 30)
    assert parse_harvest_clock("1:30pm") == time(13, 30)
    assert parse_harvest_clock("12:05am") == time(0, 5)
    assert parse_harvest_clock("12:30pm") == time(12, 30)
    assert parse_harvest_clock("") is None
    assert parse_harvest_clock("soon") is None


def test_busy_intervals_only_counts_the_same_task_with_times():
    entries = [
        entry(1, 10, "1:00", "2:30", "SF#00970001"),
        entry(2, 10, "17:30", "8:30", "", task_id=1068027),  # inactive on-call: other task
        entry(3, 10, "", "", "duration only"),
        entry(4, 10, "23:30", "0:00", "late"),  # ends at midnight
    ]
    busy = busy_intervals(entries, PROJECT, TASK, LONDON)
    assert [(s.strftime("%d %H:%M"), e.strftime("%d %H:%M"), i) for s, e, i in busy] == [
        ("10 01:00", "10 02:30", 1),
        ("10 23:30", "11 00:00", 4),
    ]


def test_free_part_of_slot():
    busy = [(at(10, 1, 0), at(10, 2, 30), 1)]
    assert free_part_of_slot(at(10, 4, 0), at(10, 5, 0), busy) == ((at(10, 4, 0), at(10, 5, 0)), None)
    assert free_part_of_slot(at(10, 1, 0), at(10, 2, 0), busy) == (None, 1)
    assert free_part_of_slot(at(10, 2, 0), at(10, 3, 0), busy) == ((at(10, 2, 30), at(10, 3, 0)), 1)
    # A short entry in the middle leaves two pieces: the longer one is kept.
    middle = [(at(10, 2, 10), at(10, 2, 20), 7)]
    assert free_part_of_slot(at(10, 2, 0), at(10, 3, 0), middle) == ((at(10, 2, 20), at(10, 3, 0)), 7)


def test_tuesday_alerts_inside_tickets_become_note_lines():
    """Tuesday 10 Nov as it first was: two SF tickets covered 01:00-04:00."""
    existing = [
        entry(101, 10, "1:00", "2:30", "SF#00977862 - USC - streambox"),
        entry(102, 10, "2:30", "4:00", "SF#00977851 - disney - cvp"),
        entry(103, 10, "17:30", "8:30", "", task_id=1068027),
    ]
    alerts = [incident(n, t) for n, t in [
        (407960, at(10, 1, 17)), (407962, at(10, 2, 11)), (407965, at(10, 3, 27)),
        (407969, at(10, 4, 39)), (407971, at(10, 5, 8)), (407977, at(10, 7, 15)),
    ]]
    items = by_number(plan_for(alerts, existing, 10))

    assert {n: (i.status, i.append_to_entry_id) for n, i in items.items() if i.status == SyncStatus.COVERED_BY_EXISTING} == {
        407960: (SyncStatus.COVERED_BY_EXISTING, 101),
        407962: (SyncStatus.COVERED_BY_EXISTING, 101),  # 02:11 is inside the USC ticket
        407965: (SyncStatus.COVERED_BY_EXISTING, 102),
    }
    pending = {n: (i.booked_start.strftime("%H:%M"), i.hours) for n, i in items.items() if i.status == SyncStatus.PENDING_CREATION}
    assert pending == {407969: ("04:00", 1.0), 407971: ("05:00", 1.0), 407977: ("07:00", 1.0)}


def test_wednesday_tickets_matching_whole_slots():
    existing = [
        entry(201, 11, "2:00", "3:00", "SF#00978094 - aqua - core"),
        entry(202, 11, "3:00", "4:00", "SF#00978085 - core - files"),
    ]
    alerts = [incident(408185, at(11, 1, 35)), incident(408187, at(11, 2, 7)), incident(408194, at(11, 3, 6))]
    items = by_number(plan_for(alerts, existing, 11))
    assert items[408185].status == SyncStatus.PENDING_CREATION
    assert (items[408187].status, items[408187].append_to_entry_id) == (SyncStatus.COVERED_BY_EXISTING, 201)
    assert (items[408194].status, items[408194].append_to_entry_id) == (SyncStatus.COVERED_BY_EXISTING, 202)


def test_partly_covered_slot_logs_only_the_gap():
    existing = [entry(301, 10, "1:00", "2:30", "SF#00970001 - ticket")]
    # Alert at 02:10 is inside the ticket: note line on the ticket + new entry for 02:30-03:00.
    inside = by_number(plan_for([incident(1, at(10, 2, 10))], existing, 10))[1]
    assert (inside.status, inside.booked_start.strftime("%H:%M"), inside.hours, inside.trimmed, inside.append_to_entry_id) == (
        SyncStatus.PENDING_CREATION, "02:30", 0.5, True, 301)
    # Alert at 02:40 is after the ticket: just the shortened entry, no note line.
    after = by_number(plan_for([incident(2, at(10, 2, 40))], existing, 10))[2]
    assert (after.booked_start.strftime("%H:%M"), after.hours, after.trimmed, after.append_to_entry_id) == ("02:30", 0.5, True, None)


def test_from_alert_time_mode_is_covered_too():
    existing = [entry(401, 10, "1:00", "3:00", "SF#00970001 - ticket")]
    item = by_number(plan_for([incident(1, at(10, 1, 20))], existing, 10, align=False))[1]
    assert (item.status, item.append_to_entry_id) == (SyncStatus.COVERED_BY_EXISTING, 401)


def test_allow_overlap_books_everything_as_before():
    existing = [entry(501, 10, "1:00", "2:30", "SF#00970001 - ticket")]
    item = by_number(plan_for([incident(1, at(10, 1, 17))], existing, 10, skip_covered=False))[1]
    assert (item.status, item.hours, item.append_to_entry_id) == (SyncStatus.PENDING_CREATION, 1.0, None)


def test_execute_adds_note_lines_and_creates_the_gap_entry():
    existing = [entry(601, 10, "1:00", "2:30", "SF#00970001 - ticket")]
    plan = plan_for([incident(1, at(10, 1, 17)), incident(2, at(10, 2, 10))], existing, 10)
    harvest = MagicMock()
    harvest.format_time_for_harvest.side_effect = lambda dt: dt.strftime("%H:%M")
    harvest.create_time_entry.return_value = HarvestTimeEntry(
        id=900, spent_date="2026-11-10", hours=0.5, notes="", project_id=PROJECT, task_id=TASK
    )

    execute_sync_plan(harvest, plan, tz=LONDON)

    created = harvest.create_time_entry.call_args.kwargs
    assert (created["started_time"], created["ended_time"], created["hours"]) == ("02:30", "03:00", 0.5)
    lines = [c.args for c in harvest.append_note_line.call_args_list]
    assert lines == [(601, "PG#1 - Core - Alert 1"), (601, "PG#2 - Core - Alert 2")]
    items = by_number(plan)
    assert items[1].status == SyncStatus.ADDED_TO_EXISTING
    assert items[2].status == SyncStatus.CREATED


def test_failed_note_line_is_reported():
    existing = [entry(701, 10, "1:00", "2:30", "SF#00970001 - ticket")]
    plan = plan_for([incident(1, at(10, 1, 17))], existing, 10)
    harvest = MagicMock()
    harvest.append_note_line.side_effect = RuntimeError("403 Forbidden")
    execute_sync_plan(harvest, plan, tz=LONDON)
    item = plan.items[0]
    assert item.status == SyncStatus.FAILED and "403 Forbidden" in item.error_message


def test_note_line_makes_the_alert_count_as_logged_next_time():
    ticket = entry(801, 10, "1:00", "2:30", "SF#00970001 - ticket\nPG#407960 - Go Response - Sohonet Fault Report")
    assert is_incident_already_logged(incident(407960, at(10, 1, 17)), [ticket]) is ticket


def test_append_note_line_adds_a_new_line_once():
    client = HarvestClient(access_token="t", account_id="1")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://api.harvestapp.com/v2/time_entries/5", json={"id": 5, "notes": "SF#00970001 - ticket"})
        rsps.add(responses.PATCH, "https://api.harvestapp.com/v2/time_entries/5", json={"id": 5})
        client.append_note_line(5, "PG#9 - Core - Alert")
        assert rsps.calls[1].request.body == b'{"notes": "SF#00970001 - ticket\\nPG#9 - Core - Alert"}'
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://api.harvestapp.com/v2/time_entries/5", json={"id": 5, "notes": "SF#00970001\nPG#9 - Core - Alert"})
        client.append_note_line(5, "PG#9 - Core - Alert")  # already there: no PATCH
        assert len(rsps.calls) == 1


def test_cli_preview_and_apply_covered_only(tmp_path, monkeypatch):
    from autoharvest.cli import cli

    monkeypatch.delenv("ALIGN_TO_CLOCK", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        f"PAGERDUTY_API_TOKEN=t\nHARVEST_ACCESS_TOKEN=t\nHARVEST_ACCOUNT_ID=1\nHARVEST_PROJECT_ID={PROJECT}\n"
        f"HARVEST_TASK_ID={TASK}\nTIMEZONE=Europe/London\nALIGN_TO_CLOCK=true\n"
    )
    with patch("autoharvest.cli.PagerDutyClient") as pd_cls, patch("autoharvest.cli.HarvestClient") as h_cls:
        pd = pd_cls.return_value
        pd.get_current_user.return_value = PagerDutyUser(id="U1", name="Davy", email="d@x", time_zone="UTC")
        pd.get_oncall_shifts.return_value = []
        pd.get_acknowledged_incidents.return_value = [incident(1, at(10, 1, 17))]
        harvest = h_cls.return_value
        harvest.get_time_entries.return_value = [entry(901, 10, "1:00", "2:30", "SF#00970001 - ticket")]
        harvest.company_config_error = None
        args = ["--config", str(env), "sync", "--since", "2026-11-10", "--until", "2026-11-10", "--all-alerts"]

        preview = CliRunner(env={"COLUMNS": "200"}).invoke(cli, args + ["--dry-run"])
        assert preview.exit_code == 0, preview.output
        assert "Add to entry 901" in preview.output
        assert "already logged in Harvest!" not in preview.output

        applied = CliRunner(env={"COLUMNS": "200"}).invoke(cli, args + ["--apply"])
        assert applied.exit_code == 0, applied.output
        harvest.append_note_line.assert_called_once_with(901, "PG#1 - Core - Alert 1")
        harvest.create_time_entry.assert_not_called()
        assert "Added to entry 901" in applied.output


def test_logged_hours_count_each_entry_once():
    """Saturday 7 Nov: two alert lines on one 2h ticket must not count as 4h."""
    ticket = entry(1001, 7, "1:00", "3:00", "SF#00977519 - wild card - vpn cert\n"
                   "PG#407582 - Go Response - Sohonet Fault Report\nPG#407589 - Webservers - SSL Certificate")
    ticket.hours = 2.0
    other = entry(1002, 7, "3:00", "4:00", "SF#00977532 - cvf - group\nPG#407590 - Datacenters - AD unreachable")
    other.hours = 1.0
    alerts = [incident(407582, at(7, 1, 6)), incident(407589, at(7, 2, 36)), incident(407590, at(7, 3, 30))]
    plan = plan_for(alerts, [ticket, other], 7)
    assert [i.status for i in plan.items] == [SyncStatus.ALREADY_LOGGED] * 3
    assert plan.total_logged_hours == 3.0
