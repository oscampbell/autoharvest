from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    hours_per_alert: float = 1.0,
    tz: ZoneInfo = ZoneInfo("UTC"),
    window_hours: float = 1.0,
    chunk_days: float = 1.0,
    progress_callback: Optional[Callable[[datetime, datetime, int], None]] = None,
) -> SyncPlan:
    """
    Fetch acknowledged alerts and existing Harvest entries, and construct a SyncPlan.
    Applies the rule:
    - Each alert creates 1 hour of bookable time.
    - Any alerts that happen within that hour are ignored.
    - The next bookable hour starts with the next alert that happens after the end of the first bookable hour.
    """
    # Look back window_hours before 'since' to catch any active window that started prior to 'since'
    fetch_since = since - timedelta(hours=window_hours)
    all_incidents = pd_client.get_acknowledged_incidents(
        user_id=user_id,
        since=fetch_since,
        until=until,
        tz=tz,
        chunk_days=chunk_days,
        progress_callback=progress_callback,
    )
    # Sort chronologically by event_time
    all_incidents.sort(key=lambda inc: inc.event_time)

    # 2. Fetch existing Harvest entries for the window
    from_date = since.astimezone(tz).strftime("%Y-%m-%d")
    to_date = until.astimezone(tz).strftime("%Y-%m-%d")
    existing_entries = harvest_client.get_time_entries(from_date=from_date, to_date=to_date)

    # 3. Apply 1-hour windowing
    items: list[SyncItem] = []
    current_window_end: Optional[datetime] = None
    window_source: Optional[AcknowledgedIncident] = None

    for inc in all_incidents:
        alert_time = inc.event_time
        in_requested_range = alert_time >= since

        if current_window_end is None or alert_time >= current_window_end:
            # Starts a new bookable window!
            current_window_end = alert_time + timedelta(hours=window_hours)
            window_source = inc

            if in_requested_range:
                existing = is_incident_already_logged(inc, existing_entries)
                if existing:
                    items.append(
                        SyncItem(
                            incident=inc,
                            hours=existing.hours,
                            status=SyncStatus.ALREADY_LOGGED,
                            harvest_entry_id=existing.id,
                        )
                    )
                else:
                    items.append(
                        SyncItem(
                            incident=inc,
                            hours=hours_per_alert,
                            status=SyncStatus.PENDING_CREATION,
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
                    )
                )

    return SyncPlan(
        since=since,
        until=until,
        items=items,
        project_id=project_id,
        task_id=task_id,
        hours_per_alert=hours_per_alert,
    )


def execute_sync_plan(
    harvest_client: HarvestClient,
    plan: SyncPlan,
) -> list[SyncItem]:
    """Execute creation of pending Harvest time entries."""
    for item in plan.pending_items:
        ext_ref = {
            "id": item.incident.incident_id,
            "permalink": item.incident.html_url,
        }
        try:
            entry = harvest_client.create_time_entry(
                project_id=plan.project_id,
                task_id=plan.task_id,
                spent_date=item.incident.spent_date,
                hours=item.hours,
                notes=item.formatted_note,
                external_reference=ext_ref,
            )
            item.status = SyncStatus.CREATED
            item.harvest_entry_id = entry.id
        except Exception as e:
            item.status = SyncStatus.FAILED
            item.error_message = str(e)

    return plan.items
