from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from autoharvest.harvest import HarvestClient
from autoharvest.models import (
    AcknowledgedIncident,
    HarvestTimeEntry,
    PagerDutyShift,
    SyncItem,
    SyncPlan,
    SyncStatus,
)
from autoharvest.pagerduty import PagerDutyClient


def parse_datetime(dt_str: str, tz: ZoneInfo) -> datetime:
    """Parse a datetime string (ISO 8601 or YYYY-MM-DD) into an aware datetime."""
    s = dt_str.strip()
    if len(s) == 10 and s.count("-") == 2:
        # Date only: YYYY-MM-DD
        d = datetime.strptime(s, "%Y-%m-%d").date()
        return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)

    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def is_incident_already_logged(incident: AcknowledgedIncident, harvest_entries: list[HarvestTimeEntry]) -> Optional[HarvestTimeEntry]:
    """
    Check if a PagerDuty incident has already been logged in Harvest.
    Matches via:
    1. external_reference_id == incident.incident_id
    2. notes contain PG#{incident_number}
    3. notes contain [PD-{incident_id}]
    4. notes contain #{incident_number} and match spent_date
    """
    pg_tag = f"PG#{incident.incident_number}"
    pd_tag = f"[PD-{incident.incident_id}]"
    inc_num_tag = f"#{incident.incident_number}"

    for entry in harvest_entries:
        if entry.external_reference_id and entry.external_reference_id == incident.incident_id:
            return entry
        if pg_tag in entry.notes:
            return entry
        if pd_tag in entry.notes:
            return entry
        if inc_num_tag in entry.notes and entry.spent_date == incident.spent_date:
            return entry

    return None


def is_sociable_hours(dt: datetime, tz: ZoneInfo) -> bool:
    """
    Check if a datetime falls within sociable on-call hours.

    Policy:
    - Sociable hours: 08:30 to 00:00 (midnight) – Weekends & 17:30 to 00:00 (midnight) Weekdays
    - Unsociable hours: All other times (e.g. overnight 00:00 to 08:30 on weekends,
      00:00 to 17:30 on weekdays).
    """
    local_dt = dt.astimezone(tz)
    local_time = local_dt.time()
    weekday = local_dt.weekday()  # Monday=0 ... Sunday=6

    if weekday >= 5:
        # Weekend (Saturday & Sunday)
        return local_time >= time(8, 30)
    else:
        # Weekday (Monday to Friday)
        return local_time >= time(17, 30)


def clock_slot_start(dt: datetime, hours: float, tz: ZoneInfo) -> datetime:
    """
    Start of the clock-aligned slot that `dt` falls in, counting slots of `hours` from local
    midnight. With 1h slots 04:37 -> 04:00; with 0.5h slots 17:42 -> 17:30.
    """
    local = dt.astimezone(tz)
    slot_minutes = max(1, round(hours * 60))
    minutes = local.hour * 60 + local.minute
    start_minutes = minutes - minutes % slot_minutes
    return local.replace(
        hour=start_minutes // 60, minute=start_minutes % 60, second=0, microsecond=0
    )


def is_alert_during_shift(
    alert: AcknowledgedIncident,
    shifts: list[PagerDutyShift],
) -> tuple[bool, Optional[PagerDutyShift]]:
    """
    Check if an alert occurred during any of the user's scheduled on-call shifts.
    Matches if either the event/trigger time or ack time falls within [shift.start, shift.end].
    """
    for shift in shifts:
        if shift.start <= alert.event_time <= shift.end:
            return True, shift
        if shift.start <= alert.acknowledged_at <= shift.end:
            return True, shift
    return False, None


def resolve_shift_window(
    pd_client: PagerDutyClient,
    user_id: str,
    tz: ZoneInfo,
    lookback_days: int = 30,
) -> tuple[datetime, datetime, PagerDutyShift]:
    """Find the user's most recent or active on-call shift."""
    now = datetime.now(tz)
    since = now - timedelta(days=lookback_days)
    until = now + timedelta(days=7)

    shifts = pd_client.get_oncall_shifts(user_id, since=since, until=until)
    if not shifts:
        raise ValueError(
            f"No on-call shifts found for user {user_id} in the last {lookback_days} days. "
            "Specify explicit dates with --since and --until, or use --days."
        )

    # Prefer currently active shift, otherwise the most recently completed shift
    active_shifts = [s for s in shifts if s.start <= now <= s.end]
    if active_shifts:
        chosen = active_shifts[-1]
    else:
        past_shifts = [s for s in shifts if s.end <= now]
        if past_shifts:
            chosen = past_shifts[-1]
        else:
            chosen = shifts[0]

    return chosen.start, min(chosen.end, now), chosen


def create_sync_plan(
    pd_client: PagerDutyClient,
    harvest_client: HarvestClient,
    user_id: str,
    since: datetime,
    until: datetime,
    project_id: int,
    task_id: int,
    hours_per_alert: Optional[float] = None,
    sociable_hours: float = 0.5,
    unsociable_hours: float = 1.0,
    tz: ZoneInfo = ZoneInfo("UTC"),
    window_hours: Optional[float] = None,
    chunk_days: float = 1.0,
    filter_oncall_shifts: bool = True,
    include_non_oncall: bool = False,
    align_to_clock: bool = False,
    progress_callback: Optional[Callable[[datetime, datetime, int], None]] = None,
) -> SyncPlan:
    """
    Fetch acknowledged alerts and existing Harvest entries, and construct a SyncPlan.
    Applies the rules:
    - Filters alerts to only those that occurred during scheduled PagerDuty on-call shifts.
    - Sociable hours (Weekends 08:30-00:00, Weekdays 17:30-00:00) create 0.5 hours of bookable time.
    - Unsociable hours (all other times, e.g. overnight 00:00-08:30 weekends, 00:00-17:30 weekdays) create 1.0 hour.
    - Any alerts that happen within that active window (0.5h or 1.0h) are ignored.
    - The next bookable hour/half-hour starts with the next alert after the end of the previous window.

    With `align_to_clock`, each alert is booked as the clock-aligned hour/half-hour it falls in
    instead (e.g. 04:37 -> 04:00-05:00), and further alerts inside that slot are ignored.
    """
    if hours_per_alert is not None:
        eff_sociable = hours_per_alert
        eff_unsociable = hours_per_alert
    else:
        eff_sociable = sociable_hours
        eff_unsociable = unsociable_hours

    max_lookback_hours = max(eff_sociable, eff_unsociable, window_hours or 1.0)
    fetch_since = since - timedelta(hours=max_lookback_hours)

    # 1. Fetch scheduled on-call shifts for this user in the window
    shifts = pd_client.get_oncall_shifts(user_id=user_id, since=fetch_since, until=until)

    # 2. Fetch acknowledged alerts
    all_incidents = pd_client.get_acknowledged_incidents(
        user_id=user_id,
        since=fetch_since,
        until=until,
        tz=tz,
        chunk_days=chunk_days,
        progress_callback=progress_callback,
    )
    all_incidents.sort(key=lambda inc: inc.event_time)

    # 3. Fetch existing Harvest entries for the window
    from_date = since.astimezone(tz).strftime("%Y-%m-%d")
    to_date = until.astimezone(tz).strftime("%Y-%m-%d")
    existing_entries = harvest_client.get_time_entries(from_date=from_date, to_date=to_date)

    # 4. Apply on-call shift filtering and windowing
    items: list[SyncItem] = []
    current_window_end: Optional[datetime] = None
    window_source: Optional[AcknowledgedIncident] = None
    current_window_duration: float = 1.0
    non_oncall_count = 0

    for inc in all_incidents:
        alert_time = inc.event_time
        in_requested_range = alert_time >= since
        is_soc = is_sociable_hours(alert_time, tz)

        # Filter: was the user scheduled on-call when this alert occurred?
        if filter_oncall_shifts:
            is_oncall, matching_shift = (
                is_alert_during_shift(inc, shifts) if shifts else (False, None)
            )
        else:
            is_oncall, matching_shift = True, None

        if not is_oncall:
            # Alert occurred outside of on-call hours (e.g. normal working day or week off)
            if in_requested_range:
                non_oncall_count += 1
                if include_non_oncall:
                    items.append(
                        SyncItem(
                            incident=inc,
                            hours=0.0,
                            status=SyncStatus.IGNORED_NOT_ONCALL,
                            shift=None,
                            is_sociable=is_soc,
                            window_hours=0.0,
                        )
                    )
            continue

        # Alert occurred during an on-call shift!
        expected_hours = eff_sociable if is_soc else eff_unsociable
        win_dur = window_hours if window_hours is not None else expected_hours

        if current_window_end is None or alert_time >= current_window_end:
            # Starts a new bookable window!
            booked_start: Optional[datetime] = None
            if align_to_clock:
                booked_start = clock_slot_start(alert_time, expected_hours, tz)
                if current_window_end is not None and booked_start < current_window_end:
                    # Never overlap the previous booking (only possible with unusual slot sizes).
                    booked_start = current_window_end
                win_dur = expected_hours
                current_window_end = booked_start + timedelta(hours=win_dur)
            else:
                current_window_end = alert_time + timedelta(hours=win_dur)
            window_source = inc
            current_window_duration = win_dur

            if in_requested_range:
                existing = is_incident_already_logged(inc, existing_entries)
                if existing:
                    items.append(
                        SyncItem(
                            incident=inc,
                            hours=existing.hours,
                            status=SyncStatus.ALREADY_LOGGED,
                            harvest_entry_id=existing.id,
                            shift=matching_shift,
                            is_sociable=is_soc,
                            window_hours=current_window_duration,
                            booked_start=booked_start,
                        )
                    )
                else:
                    items.append(
                        SyncItem(
                            incident=inc,
                            hours=expected_hours,
                            status=SyncStatus.PENDING_CREATION,
                            shift=matching_shift,
                            is_sociable=is_soc,
                            window_hours=current_window_duration,
                            booked_start=booked_start,
                        )
                    )
        else:
            # Within the active window of window_source -> ignored
            if in_requested_range:
                items.append(
                    SyncItem(
                        incident=inc,
                        hours=0.0,
                        status=SyncStatus.IGNORED_WINDOW,
                        ignored_by_incident=window_source,
                        shift=matching_shift,
                        is_sociable=is_soc,
                        window_hours=current_window_duration,
                    )
                )

    return SyncPlan(
        since=since,
        until=until,
        items=items,
        project_id=project_id,
        task_id=task_id,
        hours_per_alert=hours_per_alert if hours_per_alert is not None else eff_unsociable,
        sociable_hours=eff_sociable,
        unsociable_hours=eff_unsociable,
        shifts=shifts,
        non_oncall_ignored_count=non_oncall_count,
    )


def execute_sync_plan(
    harvest_client: HarvestClient,
    plan: SyncPlan,
    tz: Optional[ZoneInfo] = None,
) -> list[SyncItem]:
    """Execute creation of pending Harvest time entries as completed segments."""
    effective_tz = tz or plan.since.tzinfo or ZoneInfo("UTC")

    for item in plan.pending_items:
        ext_ref = {
            "id": item.incident.incident_id,
            "permalink": item.incident.html_url,
        }

        # Calculate start and end times in user's target timezone
        start_dt = (item.booked_start or item.incident.event_time).astimezone(effective_tz)
        end_dt = start_dt + timedelta(hours=item.hours)
        started_time_str = harvest_client.format_time_for_harvest(start_dt)
        ended_time_str = harvest_client.format_time_for_harvest(end_dt)
        spent_date = start_dt.strftime("%Y-%m-%d")

        try:
            entry = harvest_client.create_time_entry(
                project_id=plan.project_id,
                task_id=plan.task_id,
                spent_date=spent_date,
                hours=item.hours,
                notes=item.formatted_note,
                started_time=started_time_str,
                ended_time=ended_time_str,
                external_reference=ext_ref,
            )
            item.status = SyncStatus.CREATED
            item.harvest_entry_id = entry.id
        except Exception as e:
            item.status = SyncStatus.FAILED
            item.error_message = str(e)

    return plan.items
