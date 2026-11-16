from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from autoharvest.harvest import HarvestClient
from autoharvest.models import (
    HarvestTimeEntry,
    OvertimeCategorySummary,
    OvertimeItem,
    OvertimeReport,
)


def parse_month_string(month_str: str, default_year: int) -> tuple[int, int]:
    """
    Parse a month specification string into (year, month).
    Supports:
    - 'YYYY-MM' or 'YYYY/MM' (e.g. '2026-10')
    - 'MM' or 'M' (e.g. '10' or '5')
    - Month names or abbreviations (e.g. 'october', 'oct', 'nov')
    """
    s = month_str.strip().lower()

    # Case 1: YYYY-MM or YYYY/MM
    if "-" in s or "/" in s:
        sep = "-" if "-" in s else "/"
        parts = s.split(sep)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            year, month = int(parts[0]), int(parts[1])
            if 1 <= month <= 12:
                return year, month

    # Case 2: Numeric month (1-12)
    if s.isdigit():
        month = int(s)
        if 1 <= month <= 12:
            return default_year, month

    # Case 3: Month name (e.g. October, Oct)
    month_names = {name.lower(): idx for idx, name in enumerate(calendar.month_name) if name}
    month_abbrs = {abbr.lower(): idx for idx, abbr in enumerate(calendar.month_abbr) if abbr}

    if s in month_names:
        return default_year, month_names[s]
    if s in month_abbrs:
        return default_year, month_abbrs[s]

    raise ValueError(
        f"Invalid month format: '{month_str}'. "
        "Use YYYY-MM (e.g. '2026-10'), month number (e.g. '10'), or month name (e.g. 'October')."
    )


def resolve_payday_cycle(
    ref_date: date,
    offset_periods: int = 0,
) -> tuple[str, str, str, str]:
    """
    Calculate the pay cycle for a given reference date and offset.

    Payroll rules:
    - 1st half of month M (Day 1-15) is paid at the end of month M.
    - 2nd half of month M (Day 16-end) is paid at the end of month M+1.
    - Pay period for the end of month M payday runs from (M-1)-16 to M-15.

    Returns:
        (from_date: 'YYYY-MM-DD', to_date: 'YYYY-MM-DD', label: str, payday_title: str)
    """
    if ref_date.day <= 15:
        p_year = ref_date.year
        p_month = ref_date.month
    else:
        if ref_date.month == 12:
            p_year = ref_date.year + 1
            p_month = 1
        else:
            p_year = ref_date.year
            p_month = ref_date.month + 1

    total_m = (p_year * 12 + (p_month - 1)) + offset_periods
    payday_year = total_m // 12
    payday_month = (total_m % 12) + 1

    cutoff_date = date(payday_year, payday_month, 15)

    prev_m = total_m - 1
    prev_y = prev_m // 12
    prev_month = (prev_m % 12) + 1
    start_date = date(prev_y, prev_month, 16)

    payday_name = date(payday_year, payday_month, 1).strftime("%B %Y")
    payday_title = f"End of {payday_name} Payday"
    if start_date.year != cutoff_date.year:
        period_str = f"{start_date.strftime('%d %b %Y')} – {cutoff_date.strftime('%d %b %Y')}"
    else:
        period_str = f"{start_date.strftime('%d %b')} – {cutoff_date.strftime('%d %b %Y')}"

    if start_date <= ref_date <= cutoff_date:
        label = f"{payday_title} ({period_str}) (accrued to date)"
    else:
        label = f"{payday_title} ({period_str})"

    return (
        start_date.strftime("%Y-%m-%d"),
        cutoff_date.strftime("%Y-%m-%d"),
        label,
        payday_title,
    )


def resolve_payday_for_target_month(
    target_year: int,
    target_month: int,
    ref_date: Optional[date] = None,
) -> tuple[str, str, str, str]:
    """Calculate the pay cycle for a specific payday month (e.g. End of November Payday runs 16 Oct – 15 Nov)."""
    cutoff_date = date(target_year, target_month, 15)
    total_m = target_year * 12 + (target_month - 1) - 1
    prev_y = total_m // 12
    prev_month = (total_m % 12) + 1
    start_date = date(prev_y, prev_month, 16)

    payday_name = date(target_year, target_month, 1).strftime("%B %Y")
    payday_title = f"End of {payday_name} Payday"
    if start_date.year != cutoff_date.year:
        period_str = f"{start_date.strftime('%d %b %Y')} – {cutoff_date.strftime('%d %b %Y')}"
    else:
        period_str = f"{start_date.strftime('%d %b')} – {cutoff_date.strftime('%d %b %Y')}"

    if ref_date and start_date <= ref_date <= cutoff_date:
        label = f"{payday_title} ({period_str}) (accrued to date)"
    else:
        label = f"{payday_title} ({period_str})"

    return (
        start_date.strftime("%Y-%m-%d"),
        cutoff_date.strftime("%Y-%m-%d"),
        label,
        payday_title,
    )


def resolve_overtime_period(
    month: Optional[str] = None,
    payday: Optional[str] = None,
    last_payday: bool = False,
    last_month: bool = False,
    this_month: bool = False,
    calendar_mode: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
    tz: Optional[ZoneInfo] = None,
    ref_date: Optional[date] = None,
) -> tuple[str, str, str]:
    """
    Determine start date, end date, and descriptive label for an overtime query.

    Default:
    Calculates the pay cycle accrued for the NEXT COMING PAYDAY:
    - 1st half of month M (1-15): paid end of month M.
    - 2nd half of month M (16-end): paid end of month M+1.
    - Cycle runs from (M-1)-16 to M-15.
    """
    target_tz = tz or ZoneInfo("UTC")
    today = ref_date or datetime.now(target_tz).date()

    # 1. Custom date range
    if since:
        from_date = since.strip()
        to_date = until.strip() if until else today.strftime("%Y-%m-%d")
        return from_date, to_date, f"{from_date} to {to_date}"

    # 2. Previous payday
    if last_payday:
        from_d, to_d, label, _ = resolve_payday_cycle(today, offset_periods=-1)
        return from_d, to_d, label

    # 3. Explicit payday specified
    if payday:
        year, month_num = parse_month_string(payday, default_year=today.year)
        from_d, to_d, label, _ = resolve_payday_for_target_month(year, month_num, ref_date=today)
        return from_d, to_d, label

    # 4. Calendar month modes
    if calendar_mode:
        if last_month:
            first_of_this_month = today.replace(day=1)
            last_day_of_last_month = first_of_this_month - timedelta(days=1)
            first_day_of_last_month = last_day_of_last_month.replace(day=1)
            return (
                first_day_of_last_month.strftime("%Y-%m-%d"),
                last_day_of_last_month.strftime("%Y-%m-%d"),
                f"Calendar Month: {first_day_of_last_month.strftime('%B %Y')}",
            )
        if month:
            year, month_num = parse_month_string(month, default_year=today.year)
            last_day = calendar.monthrange(year, month_num)[1]
            start_d = date(year, month_num, 1)
            end_d = date(year, month_num, last_day)
            return (
                start_d.strftime("%Y-%m-%d"),
                end_d.strftime("%Y-%m-%d"),
                f"Calendar Month: {start_d.strftime('%B %Y')}",
            )
        # this_month in calendar mode
        first_of_this_month = today.replace(day=1)
        return (
            first_of_this_month.strftime("%Y-%m-%d"),
            today.strftime("%Y-%m-%d"),
            f"Calendar Month: {today.strftime('%B %Y')} (to date)",
        )

    # 5. If user passed --last-month without --calendar, treat as previous payday
    if last_month:
        from_d, to_d, label, _ = resolve_payday_cycle(today, offset_periods=-1)
        return from_d, to_d, label

    # 6. If user passed --month without --calendar, resolve the payday for that month
    if month:
        year, month_num = parse_month_string(month, default_year=today.year)
        from_d, to_d, label, _ = resolve_payday_for_target_month(year, month_num, ref_date=today)
        return from_d, to_d, label

    # 7. DEFAULT: Next coming payday!
    from_d, to_d, label, _ = resolve_payday_cycle(today, offset_periods=0)
    return from_d, to_d, label


def classify_overtime_task(
    task_name: str,
    project_name: Optional[str],
    rates: dict[str, float],
) -> Optional[tuple[str, float]]:
    """
    Categorize a Harvest task and determine its hourly overtime rate.
    Default rates:
    - Inactive on-call: £6/hr
    - Active on-call: £30/hr
    - Out of hours configuration: £30/hr
    Returns None for tasks that are not overtime (they are excluded from the report).
    """
    t_lower = (task_name or "").lower().strip()
    rate_inactive = rates.get("inactive", 6.0)
    rate_active = rates.get("active", 30.0)
    rate_ooh = rates.get("out_of_hours", 30.0)

    if "inactive" in t_lower:
        return "1st On Call - Inactive", rate_inactive
    elif "out of hours" in t_lower or "ooh" in t_lower:
        return "Out of Hours Configuration", rate_ooh
    elif "active" in t_lower or "on call" in t_lower or "on-call" in t_lower:
        return "1st On Call - Active", rate_active
    else:
        # Not an on-call / out-of-hours task (e.g. normal project work): not overtime.
        return None


def calculate_overtime_report(
    harvest_client: HarvestClient,
    start_date: str,
    end_date: str,
    period_label: str,
    rates: dict[str, float],
    currency_symbol: str = "£",
) -> OvertimeReport:
    """Fetch Harvest time entries for the period and compile an OvertimeReport."""
    # Scope to the current Harvest user: admin/manager tokens otherwise return everyone's entries.
    harvest_user_id = harvest_client.get_current_user().id
    entries = harvest_client.get_time_entries(
        from_date=start_date, to_date=end_date, user_id=harvest_user_id
    )
    # Sort entries chronologically by spent_date
    entries.sort(key=lambda e: (e.spent_date, e.id))

    items: list[OvertimeItem] = []
    category_buckets: dict[tuple[str, str, int, float], list[OvertimeItem]] = {}
    excluded_hours: dict[str, float] = {}

    for entry in entries:
        tname = entry.task_name or f"Task {entry.task_id}"
        pname = entry.project_name or f"Project {entry.project_id}"
        classified = classify_overtime_task(tname, pname, rates)
        if classified is None:
            # Not overtime, but report it so a misnamed on-call task isn't silently lost.
            key = f"{pname} / {tname}"
            excluded_hours[key] = excluded_hours.get(key, 0.0) + entry.hours
            continue
        category, rate = classified

        item = OvertimeItem(entry=entry, category=category, hourly_rate=rate)
        items.append(item)

        bucket_key = (category, pname, entry.task_id, rate)
        category_buckets.setdefault(bucket_key, []).append(item)

    summaries: list[OvertimeCategorySummary] = []
    for (category, pname, task_id, rate), bucket_items in category_buckets.items():
        cat_hours = sum(i.entry.hours for i in bucket_items)
        cat_earned = sum(i.total_earned for i in bucket_items)
        t_name = bucket_items[0].entry.task_name or str(task_id)
        summaries.append(
            OvertimeCategorySummary(
                category=category,
                project_name=pname,
                task_name=t_name,
                task_id=task_id,
                hourly_rate=rate,
                total_hours=cat_hours,
                total_earned=cat_earned,
                entry_count=len(bucket_items),
            )
        )

    # Sort categories: Active first, then Out of hours, then Inactive, then by earnings
    def sort_key(s: OvertimeCategorySummary) -> tuple[int, float]:
        name_lower = s.category.lower()
        # Check "inactive" before "active": "active" is a substring of "inactive".
        if "inactive" in name_lower:
            rank = 3
        elif "active" in name_lower:
            rank = 1
        elif "out of hours" in name_lower:
            rank = 2
        else:
            rank = 4
        return (rank, -s.total_earned)

    summaries.sort(key=sort_key)

    total_hours = sum(i.entry.hours for i in items)
    total_earned = sum(i.total_earned for i in items)

    return OvertimeReport(
        start_date=start_date,
        end_date=end_date,
        period_label=period_label,
        items=items,
        categories=summaries,
        total_hours=total_hours,
        total_earned=total_earned,
        currency_symbol=currency_symbol,
        excluded_hours=excluded_hours,
    )
