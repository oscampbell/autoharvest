from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
import pytest

from autoharvest.models import HarvestTimeEntry
from autoharvest.overtime import (
    calculate_overtime_report,
    classify_overtime_task,
    parse_month_string,
    resolve_overtime_period,
    resolve_payday_cycle,
    resolve_payday_for_target_month,
)


def test_parse_month_string():
    # YYYY-MM
    assert parse_month_string("2026-10", 2026) == (2026, 10)
    assert parse_month_string("2025/05", 2026) == (2025, 5)

    # Numeric month
    assert parse_month_string("10", 2026) == (2026, 10)
    assert parse_month_string("3", 2026) == (2026, 3)

    # Month name
    assert parse_month_string("October", 2026) == (2026, 10)
    assert parse_month_string("october", 2026) == (2026, 10)
    assert parse_month_string("nov", 2026) == (2026, 11)
    assert parse_month_string("Jan", 2026) == (2026, 1)

    # Invalid
    with pytest.raises(ValueError):
        parse_month_string("invalid_month", 2026)

    with pytest.raises(ValueError):
        parse_month_string("13", 2026)


def test_resolve_payday_cycle():
    # Date on or before 15th (e.g. Nov 6) -> End of Nov Payday (16 Oct to 15 Nov)
    ref1 = date(2026, 11, 6)
    start1, end1, label1, title1 = resolve_payday_cycle(ref1)
    assert start1 == "2026-10-16"
    assert end1 == "2026-11-15"
    assert title1 == "End of November 2026 Payday"
    assert "(accrued to date)" in label1

    # Date after 15th (e.g. Nov 23) -> End of Dec Payday (16 Nov to 15 Dec)
    ref2 = date(2026, 11, 23)
    start2, end2, label2, title2 = resolve_payday_cycle(ref2)
    assert start2 == "2026-11-16"
    assert end2 == "2026-12-15"
    assert title2 == "End of December 2026 Payday"
    assert "(accrued to date)" in label2

    # Previous payday cycle (offset=-1) from Nov 6 -> End of Oct Payday (16 Sep to 15 Oct)
    start_prev, end_prev, label_prev, title_prev = resolve_payday_cycle(ref1, offset_periods=-1)
    assert start_prev == "2026-09-16"
    assert end_prev == "2026-10-15"
    assert title_prev == "End of October 2026 Payday"
    assert "(accrued to date)" not in label_prev

    # Year boundary transition (Dec 20) -> End of Jan 2027 Payday (16 Dec 2026 to 15 Jan 2027)
    ref_yearend = date(2026, 12, 20)
    start_y, end_y, label_y, title_y = resolve_payday_cycle(ref_yearend)
    assert start_y == "2026-12-16"
    assert end_y == "2027-01-15"
    assert title_y == "End of January 2027 Payday"
    assert "16 Dec 2026 – 15 Jan 2027" in label_y


def test_resolve_payday_for_target_month():
    start, end, label, title = resolve_payday_for_target_month(2026, 11)
    assert start == "2026-10-16"
    assert end == "2026-11-15"
    assert title == "End of November 2026 Payday"
    assert "16 Oct – 15 Nov 2026" in label

    # January target month spans across previous year
    start_jan, end_jan, label_jan, title_jan = resolve_payday_for_target_month(2027, 1)
    assert start_jan == "2026-12-16"
    assert end_jan == "2027-01-15"
    assert title_jan == "End of January 2027 Payday"
    assert "16 Dec 2026 – 15 Jan 2027" in label_jan


def test_resolve_overtime_period():
    tz = ZoneInfo("UTC")
    ref_date = date(2026, 11, 6)

    # 1. Custom since and until
    start, end, label = resolve_overtime_period(since="2026-05-01", until="2026-05-15", tz=tz, ref_date=ref_date)
    assert start == "2026-05-01"
    assert end == "2026-05-15"
    assert "2026-05-01 to 2026-05-15" in label

    # 2. Default: next coming payday (for Nov 6, it is End of Nov Payday 16 Oct – 15 Nov)
    start, end, label = resolve_overtime_period(tz=tz, ref_date=ref_date)
    assert start == "2026-10-16"
    assert end == "2026-11-15"
    assert "End of November 2026 Payday" in label
    assert "(accrued to date)" in label

    # 3. Explicit payday
    start, end, label = resolve_overtime_period(payday="2026-10", tz=tz, ref_date=ref_date)
    assert start == "2026-09-16"
    assert end == "2026-10-15"
    assert "End of October 2026 Payday" in label

    # 4. Last payday
    start, end, label = resolve_overtime_period(last_payday=True, tz=tz, ref_date=ref_date)
    assert start == "2026-09-16"
    assert end == "2026-10-15"
    assert "End of October 2026 Payday" in label

    # 5. Month option without calendar flag defaults to payday cycle
    start, end, label = resolve_overtime_period(month="October", tz=tz, ref_date=ref_date)
    assert start == "2026-09-16"
    assert end == "2026-10-15"

    # 6. Calendar mode: specific month
    start, end, label = resolve_overtime_period(month="2026-10", calendar_mode=True, tz=tz, ref_date=ref_date)
    assert start == "2026-10-01"
    assert end == "2026-10-31"
    assert "Calendar Month: October 2026" in label

    # 7. Calendar mode: last month
    start, end, label = resolve_overtime_period(last_month=True, calendar_mode=True, tz=tz, ref_date=ref_date)
    assert start == "2026-10-01"
    assert end == "2026-10-31"
    assert "Calendar Month: October 2026" in label

    # 8. Calendar mode: this month
    start, end, label = resolve_overtime_period(this_month=True, calendar_mode=True, tz=tz, ref_date=ref_date)
    assert start == "2026-11-01"
    assert end == "2026-11-06"
    assert "Calendar Month: November 2026 (to date)" in label


def test_classify_overtime_task():
    rates = {
        "inactive": 6.0,
        "active": 30.0,
        "out_of_hours": 30.0,
    }

    # Inactive
    cat, rate = classify_overtime_task("1st On Call - Inactive", "xOn Call InActive", rates)
    assert cat == "1st On Call - Inactive"
    assert rate == 6.0

    cat, rate = classify_overtime_task("2nd on call - Inactive", "xOn Call InActive", rates)
    assert cat == "1st On Call - Inactive"
    assert rate == 6.0

    # Active
    cat, rate = classify_overtime_task("1st On call - Active", "UK Support", rates)
    assert cat == "1st On Call - Active"
    assert rate == 30.0

    # Out of hours
    cat, rate = classify_overtime_task("Out of hours configuration", "UK Support", rates)
    assert cat == "Out of Hours Configuration"
    assert rate == 30.0

    # Non on-call work is not overtime
    assert classify_overtime_task("General Maintenance", "Internal", rates) is None
    assert classify_overtime_task("Development", "Client X", rates) is None


def test_calculate_overtime_report():
    harvest_mock = MagicMock()

    entries = [
        HarvestTimeEntry(
            id=1,
            spent_date="2026-10-05",
            hours=15.0,
            notes="",
            project_id=1,
            project_name="xOn Call InActive",
            task_id=10,
            task_name="1st On Call - Inactive",
        ),
        HarvestTimeEntry(
            id=2,
            spent_date="2026-10-05",
            hours=1.0,
            notes="PG#1001 - Core - Alert",
            project_id=2,
            project_name="UK Support",
            task_id=20,
            task_name="1st On call - Active",
        ),
        HarvestTimeEntry(
            id=3,
            spent_date="2026-10-06",
            hours=0.5,
            notes="PG#1002 - Web - Alert",
            project_id=2,
            project_name="UK Support",
            task_id=20,
            task_name="1st On call - Active",
        ),
        HarvestTimeEntry(
            id=4,
            spent_date="2026-10-10",
            hours=8.0,
            notes="Volta Migration",
            project_id=2,
            project_name="UK Support",
            task_id=30,
            task_name="Out of hours configuration",
        ),
    ]
    harvest_mock.get_time_entries.return_value = entries

    rates = {"inactive": 6.0, "active": 30.0, "out_of_hours": 30.0}
    report = calculate_overtime_report(
        harvest_client=harvest_mock,
        start_date="2026-10-01",
        end_date="2026-10-31",
        period_label="October 2026",
        rates=rates,
        currency_symbol="£",
    )

    assert report.start_date == "2026-10-01"
    assert report.end_date == "2026-10-31"
    assert report.period_label == "October 2026"
    assert len(report.items) == 4

    # Total hours: 15.0 + 1.0 + 0.5 + 8.0 = 24.5 hrs
    assert report.total_hours == 24.5

    # Total earnings:
    # Inactive: 15.0 * 6.0 = £90.00
    # Active: 1.5 * 30.0 = £45.00
    # OOH: 8.0 * 30.0 = £240.00
    # Total: £375.00
    assert report.total_earned == 375.0

    assert len(report.categories) == 3
    # Check category summaries
    active_summary = next(c for c in report.categories if "Active" in c.category)
    assert active_summary.total_hours == 1.5
    assert active_summary.total_earned == 45.0
    assert active_summary.entry_count == 2

    inactive_summary = next(c for c in report.categories if "Inactive" in c.category)
    assert inactive_summary.total_hours == 15.0
    assert inactive_summary.total_earned == 90.0
    assert inactive_summary.entry_count == 1

    ooh_summary = next(c for c in report.categories if "Out of Hours" in c.category)
    assert ooh_summary.total_hours == 8.0
    assert ooh_summary.total_earned == 240.0
    assert ooh_summary.entry_count == 1


def test_calculate_overtime_report_excludes_normal_work_and_scopes_to_user():
    harvest_mock = MagicMock()
    harvest_mock.get_current_user.return_value.id = 42
    harvest_mock.get_time_entries.return_value = [
        HarvestTimeEntry(
            id=1, spent_date="2026-10-05", hours=2.0, notes="", project_id=1,
            project_name="On Call", task_id=10, task_name="1st On Call - Inactive",
        ),
        HarvestTimeEntry(
            id=2, spent_date="2026-10-05", hours=1.0, notes="PG#1 - Core - Alert", project_id=2,
            project_name="UK Support", task_id=20, task_name="1st On call - Active",
        ),
        HarvestTimeEntry(
            id=3, spent_date="2026-10-06", hours=7.5, notes="Feature work", project_id=3,
            project_name="Client X", task_id=30, task_name="Development",
        ),
    ]

    report = calculate_overtime_report(
        harvest_client=harvest_mock,
        start_date="2026-10-01",
        end_date="2026-10-31",
        period_label="October 2026",
        rates={"inactive": 6.0, "active": 30.0, "out_of_hours": 30.0},
    )

    harvest_mock.get_time_entries.assert_called_once_with(
        from_date="2026-10-01", to_date="2026-10-31", user_id=42
    )
    assert len(report.items) == 2
    assert report.total_hours == 3.0
    assert report.total_earned == 2.0 * 6.0 + 1.0 * 30.0
    # Normal work is left out of the totals but still reported, so it isn't silently lost.
    assert report.excluded_hours == {"Client X / Development": 7.5}
    # Active sorts before Inactive ("active" is a substring of "inactive")
    assert [c.category for c in report.categories] == [
        "1st On Call - Active",
        "1st On Call - Inactive",
    ]
