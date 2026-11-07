from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo
import requests

from autoharvest.models import AcknowledgedIncident, PagerDutyShift, PagerDutyUser


class PagerDutyAPIError(Exception):
    """Raised when PagerDuty API request fails."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


# PagerDuty returns HTTP 400 once offset + limit exceeds 10,000 on classic pagination.
PAGERDUTY_MAX_OFFSET = 10000
# Smallest window the log entry fetch will split down to before giving up.
MIN_SPLIT_WINDOW = timedelta(minutes=1)


class PagerDutyClient:
    def __init__(self, token: str, base_url: str = "https://api.pagerduty.com", session: Optional[requests.Session] = None):
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Token token={self.token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.request(method, url, params=params, timeout=30)
        except requests.RequestException as e:
            raise PagerDutyAPIError(f"Network error communicating with PagerDuty: {e}") from e

        if resp.status_code == 401:
            raise PagerDutyAPIError(
                "PagerDuty authentication failed (401 Unauthorized). Please check your PAGERDUTY_API_TOKEN.",
                status_code=401,
                response_text=resp.text,
            )
        if resp.status_code == 403:
            raise PagerDutyAPIError(
                "PagerDuty authorization failed (403 Forbidden). Token lacks necessary permissions.",
                status_code=403,
                response_text=resp.text,
            )
        if not resp.ok:
            raise PagerDutyAPIError(
                f"PagerDuty API error {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                response_text=resp.text,
            )

        return resp.json()

    def get_current_user(self) -> PagerDutyUser:
        """Fetch the authenticated user's profile from /users/me."""
        data = self._request("GET", "/users/me")
        user = data.get("user", {})
        return PagerDutyUser(
            id=user.get("id", ""),
            name=user.get("name", ""),
            email=user.get("email", ""),
            time_zone=user.get("time_zone", "UTC"),
        )

    def get_user_by_id(self, user_id: str) -> PagerDutyUser:
        """Fetch a specific user profile by user ID."""
        data = self._request("GET", f"/users/{user_id}")
        user = data.get("user", {})
        return PagerDutyUser(
            id=user.get("id", ""),
            name=user.get("name", ""),
            email=user.get("email", ""),
            time_zone=user.get("time_zone", "UTC"),
        )

    def get_oncall_shifts(
        self,
        user_id: str,
        since: datetime,
        until: datetime,
    ) -> list[PagerDutyShift]:
        """
        Fetch all on-call shifts for a user within a time range.
        Paginates through /oncalls to get the complete schedule history.
        """
        limit = 100
        offset = 0
        more = True
        shifts: list[PagerDutyShift] = []

        while more and offset < 10000:
            params = {
                "user_ids[]": user_id,
                "since": since.isoformat(),
                "until": until.isoformat(),
                "limit": limit,
                "offset": offset,
            }
            data = self._request("GET", "/oncalls", params=params)
            oncalls = data.get("oncalls", [])
            more = data.get("more", False)
            offset += limit

            for item in oncalls:
                start_str = item.get("start")
                end_str = item.get("end")
                if not start_str or not end_str:
                    continue

                # PagerDuty returns ISO 8601 strings (e.g. 2026-10-15T09:00:00Z)
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                sched = item.get("schedule", {})

                shifts.append(
                    PagerDutyShift(
                        start=start_dt,
                        end=end_dt,
                        schedule_id=sched.get("id"),
                        schedule_summary=sched.get("summary"),
                        user_id=item.get("user", {}).get("id", user_id),
                    )
                )

            if len(oncalls) < limit:
                break

        # Deduplicate identical shifts across escalation levels
        unique_shifts: list[PagerDutyShift] = []
        seen = set()
        for s in sorted(shifts, key=lambda x: (x.start, x.end)):
            key = (s.start, s.end, s.schedule_id)
            if key not in seen:
                seen.add(key)
                unique_shifts.append(s)

        return unique_shifts

    def _iter_log_entry_pages(self, since: datetime, until: datetime):
        """
        Yield pages of account-wide log entries between `since` and `until`.

        PagerDuty returns HTTP 400 once offset + limit exceeds 10,000. If a window
        holds more than that, it is split in half and each half fetched instead.
        A window is only yielded once fully fetched, so a split never yields
        duplicate pages.
        """
        limit = 100
        pages: list[list[dict[str, Any]]] = []
        offset = 0
        while True:
            if offset + limit > PAGERDUTY_MAX_OFFSET:
                if until - since <= MIN_SPLIT_WINDOW:
                    raise PagerDutyAPIError(
                        f"More than {PAGERDUTY_MAX_OFFSET} PagerDuty log entries between "
                        f"{since.isoformat()} and {until.isoformat()}; results would be incomplete."
                    )
                mid = since + (until - since) / 2
                yield from self._iter_log_entry_pages(since, mid)
                yield from self._iter_log_entry_pages(mid, until)
                return
            params = {
                "since": since.isoformat(),
                "until": until.isoformat(),
                "is_overview": "true",
                "include[]": "incidents",
                "limit": limit,
                "offset": offset,
            }
            data = self._request("GET", "/log_entries", params=params)
            entries = data.get("log_entries", [])
            pages.append(entries)
            if not data.get("more", False) or len(entries) < limit:
                break
            offset += limit
        yield from pages

    def get_acknowledged_incidents(
        self,
        user_id: str,
        since: datetime,
        until: datetime,
        tz: ZoneInfo = ZoneInfo("UTC"),
        chunk_days: float = 1.0,
        progress_callback: Optional[Any] = None,
    ) -> list[AcknowledgedIncident]:
        """
        Fetch all incidents acknowledged by the user between `since` and `until`.
        PagerDuty has no per-user log entry endpoint, so this pages through the
        account-wide /log_entries and keeps only acknowledgements by this user.
        Requests are chunked by `chunk_days` (default: 1 day); a chunk that holds
        more than PagerDuty's 10,000-record pagination limit is split in half
        automatically, so results are never silently truncated.
        Deduplicates if the same incident was acknowledged multiple times.
        """
        chunk_delta = timedelta(days=chunk_days)
        incidents_by_id: dict[str, AcknowledgedIncident] = {}

        current_start = since
        while current_start < until:
            current_end = min(current_start + chunk_delta, until)

            if progress_callback:
                progress_callback(current_start, current_end, len(incidents_by_id))

            for entries in self._iter_log_entry_pages(current_start, current_end):
                for entry in entries:
                    if entry.get("type") != "acknowledge_log_entry":
                        continue

                    agent = entry.get("agent", {})
                    agent_id = agent.get("id")
                    if agent_id != user_id:
                        continue

                    incident_data = entry.get("incident", {})
                    inc_id = incident_data.get("id")
                    if not inc_id:
                        continue

                    created_at_str = entry.get("created_at")
                    if not created_at_str:
                        continue

                    ack_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                    inc_created_str = incident_data.get("created_at")
                    inc_created_dt = (
                        datetime.fromisoformat(inc_created_str.replace("Z", "+00:00"))
                        if inc_created_str
                        else None
                    )
                    event_dt = inc_created_dt or ack_dt
                    local_event_dt = event_dt.astimezone(tz)
                    spent_date = local_event_dt.strftime("%Y-%m-%d")

                    # If this incident was already seen, keep the earliest acknowledgment
                    if inc_id in incidents_by_id:
                        if ack_dt < incidents_by_id[inc_id].acknowledged_at:
                            incidents_by_id[inc_id].acknowledged_at = ack_dt
                            incidents_by_id[inc_id].incident_created_at = inc_created_dt
                            incidents_by_id[inc_id].spent_date = spent_date
                            incidents_by_id[inc_id].log_entry_id = entry.get("id", "")
                        continue

                    incidents_by_id[inc_id] = AcknowledgedIncident(
                        incident_id=inc_id,
                        incident_number=incident_data.get("incident_number", inc_id),
                        title=incident_data.get("title") or incident_data.get("summary") or "Untitled Incident",
                        service_name=incident_data.get("service", {}).get("summary", "Unknown Service"),
                        urgency=incident_data.get("urgency", "high"),
                        html_url=incident_data.get("html_url", f"https://api.pagerduty.com/incidents/{inc_id}"),
                        acknowledged_at=ack_dt,
                        spent_date=spent_date,
                        log_entry_id=entry.get("id", ""),
                        incident_created_at=inc_created_dt,
                    )

            current_start = current_end

        results = list(incidents_by_id.values())
        results.sort(key=lambda x: x.event_time)
        return results
