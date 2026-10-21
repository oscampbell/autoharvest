from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo
import requests

from autoharvest.models import AcknowledgedIncident, PagerDutyShift, PagerDutyUser


class PagerDutyAPIError(Exception):
    """Raised when PagerDuty API request fails."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


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
        """Fetch on-call shifts for a user within a time range."""
        params = {
            "user_ids[]": user_id,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "earliest": "true",
            "limit": 100,
        }
        data = self._request("GET", "/oncalls", params=params)
        oncalls = data.get("oncalls", [])
        shifts: list[PagerDutyShift] = []

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

        # Sort shifts chronologically
        shifts.sort(key=lambda s: s.start)
        return shifts

    def get_acknowledged_incidents(
        self,
        user_id: str,
        since: datetime,
        until: datetime,
        tz: ZoneInfo = ZoneInfo("UTC"),
    ) -> list[AcknowledgedIncident]:
        """
        Fetch all incidents acknowledged by the user between `since` and `until`.
        Queries /log_entries and filters by type=acknowledge_log_entry and agent.id=user_id.
        Deduplicates if the same incident was acknowledged multiple times.
        """
        limit = 100
        offset = 0
        more = True
        incidents_by_id: dict[str, AcknowledgedIncident] = {}

        while more:
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
            more = data.get("more", False)
            offset += limit

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
                # Convert to target timezone for booking date
                local_ack_dt = ack_dt.astimezone(tz)
                spent_date = local_ack_dt.strftime("%Y-%m-%d")

                # If this incident was already seen, keep the earliest acknowledgment
                if inc_id in incidents_by_id:
                    if ack_dt < incidents_by_id[inc_id].acknowledged_at:
                        incidents_by_id[inc_id].acknowledged_at = ack_dt
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
                )

            if len(entries) < limit:
                break

        results = list(incidents_by_id.values())
        results.sort(key=lambda x: x.acknowledged_at)
        return results
