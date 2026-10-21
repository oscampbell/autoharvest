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


@dataclass
class SyncItem:
    incident: AcknowledgedIncident
    hours: float
    status: SyncStatus
    harvest_entry_id: Optional[int] = None
    ignored_by_incident: Optional[AcknowledgedIncident] = None
    shift: Optional[PagerDutyShift] = None
    error_message: Optional[str] = None

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
        return sum(item.hours for item in self.already_logged_items)
