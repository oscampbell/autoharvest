from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SyncStatus(str, Enum):
    PENDING_CREATION = "pending"
    ALREADY_LOGGED = "already_logged"
    IGNORED_WINDOW = "ignored_window"
    IGNORED_NOT_ONCALL = "not_oncall"
    # The slot is fully covered by an existing entry on the same project/task, so the alert
    # will be added as a new line in that entry's notes instead of getting its own entry.
    COVERED_BY_EXISTING = "covered"
    # After execution: the alert line was added to the covering entry.
    ADDED_TO_EXISTING = "added_to_existing"
    CREATED = "created"
    FAILED = "failed"


@dataclass
class PagerDutyUser:
    id: str
    name: str
    email: str
    time_zone: str = "UTC"


@dataclass
class PagerDutyShift:
    start: datetime
    end: datetime
    schedule_id: Optional[str] = None
    schedule_summary: Optional[str] = None
    user_id: Optional[str] = None


@dataclass
class AcknowledgedIncident:
    incident_id: str
    incident_number: int | str
    title: str
    service_name: str
    urgency: str
    html_url: str
    acknowledged_at: datetime
    spent_date: str  # YYYY-MM-DD
    log_entry_id: str
    incident_created_at: Optional[datetime] = None

    @property
    def event_time(self) -> datetime:
        """Timestamp when the alert occurred (incident trigger time if available, otherwise ack time)."""
        return self.incident_created_at or self.acknowledged_at


@dataclass
class HarvestUser:
    id: int
    first_name: str
    last_name: str
    email: str

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class HarvestTask:
    id: int
    name: str


@dataclass
class HarvestProject:
    id: int
    name: str
    code: Optional[str] = None
    tasks: list[HarvestTask] = field(default_factory=list)


@dataclass
class HarvestTimeEntry:
    id: int
    spent_date: str  # YYYY-MM-DD
    hours: float
    notes: str
    project_id: int
    task_id: int
    project_name: Optional[str] = None
    task_name: Optional[str] = None
    external_reference_id: Optional[str] = None
    started_time: Optional[str] = None
    ended_time: Optional[str] = None
    is_running: bool = False


@dataclass
class SyncItem:
    incident: AcknowledgedIncident
    hours: float
    status: SyncStatus
    harvest_entry_id: Optional[int] = None
    ignored_by_incident: Optional[AcknowledgedIncident] = None
    shift: Optional[PagerDutyShift] = None
    error_message: Optional[str] = None
    is_sociable: bool = False
    window_hours: float = 1.0
    # Clock-aligned mode: where the booked segment starts (e.g. 04:00 for an alert at 04:37).
    # None means the segment starts at the alert time.
    booked_start: Optional[datetime] = None
    # Existing entry that covers the alert time: the alert is added as a line in its notes.
    append_to_entry_id: Optional[int] = None
    # True when only part of the slot was free, so the new entry was shortened to that part.
    trimmed: bool = False

    @property
    def formatted_note(self) -> str:
        inc_num = str(self.incident.incident_number).strip()
        service = self.incident.service_name.strip()
        summary = self.incident.title.strip().replace("\r\n", " ").replace("\n", " ")
        return f"PG#{inc_num} - {service} - {summary}"


@dataclass
class SyncPlan:
    since: datetime
    until: datetime
    items: list[SyncItem]
    project_id: int
    task_id: int
    hours_per_alert: float = 1.0
    sociable_hours: float = 0.5
    unsociable_hours: float = 1.0
    shifts: list[PagerDutyShift] = field(default_factory=list)
    non_oncall_ignored_count: int = 0

    @property
    def oncall_items(self) -> list[SyncItem]:
        return [item for item in self.items if item.status != SyncStatus.IGNORED_NOT_ONCALL]

    @property
    def pending_items(self) -> list[SyncItem]:
        return [item for item in self.items if item.status == SyncStatus.PENDING_CREATION]

    @property
    def already_logged_items(self) -> list[SyncItem]:
        return [item for item in self.items if item.status == SyncStatus.ALREADY_LOGGED]

    @property
    def covered_items(self) -> list[SyncItem]:
        return [item for item in self.items if item.status == SyncStatus.COVERED_BY_EXISTING]

    @property
    def actionable_items(self) -> list[SyncItem]:
        """Items that change Harvest when applied: new entries and alert lines for existing ones."""
        return self.pending_items + self.covered_items

    @property
    def ignored_items(self) -> list[SyncItem]:
        return [item for item in self.items if item.status == SyncStatus.IGNORED_WINDOW]

    @property
    def not_oncall_items(self) -> list[SyncItem]:
        return [item for item in self.items if item.status == SyncStatus.IGNORED_NOT_ONCALL]

    @property
    def bookable_items(self) -> list[SyncItem]:
        return [
            item
            for item in self.items
            if item.status in (SyncStatus.PENDING_CREATION, SyncStatus.ALREADY_LOGGED)
        ]

    @property
    def total_pending_hours(self) -> float:
        return sum(item.hours for item in self.pending_items)

    @property
    def total_logged_hours(self) -> float:
        """Hours already in Harvest, counting each entry once even if several alerts point at it."""
        seen: set[int] = set()
        total = 0.0
        for item in self.already_logged_items:
            if item.harvest_entry_id is not None:
                if item.harvest_entry_id in seen:
                    continue
                seen.add(item.harvest_entry_id)
            total += item.hours
        return total


@dataclass
class OvertimeItem:
    entry: HarvestTimeEntry
    category: str
    hourly_rate: float

    @property
    def total_earned(self) -> float:
        return self.entry.hours * self.hourly_rate


@dataclass
class OvertimeCategorySummary:
    category: str
    project_name: str
    task_name: str
    task_id: int
    hourly_rate: float
    total_hours: float
    total_earned: float
    entry_count: int


@dataclass
class OvertimeReport:
    start_date: str
    end_date: str
    period_label: str
    items: list[OvertimeItem]
    categories: list[OvertimeCategorySummary]
    total_hours: float
    total_earned: float
    currency_symbol: str = "£"


@dataclass
class EstimatedPayBreakdown:
    annual_salary: float
    base_monthly_gross: float
    overtime_gross: float
    total_monthly_gross: float

    # Base month (without overtime)
    base_tax: float
    base_ni: float
    base_net: float

    # Overtime marginal deductions
    overtime_tax: float
    overtime_ni: float
    overtime_net: float

    # Total month (base + overtime)
    total_tax: float
    total_ni: float
    total_net: float

    currency_symbol: str = "£"
