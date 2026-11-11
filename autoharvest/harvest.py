from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import requests

from autoharvest.models import HarvestProject, HarvestTask, HarvestTimeEntry, HarvestUser


class HarvestAPIError(Exception):
    """Raised when Harvest API request fails."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class HarvestClient:
    def __init__(
        self,
        access_token: str,
        account_id: str,
        user_agent: Optional[str] = None,
        base_url: str = "https://api.harvestapp.com/v2",
        session: Optional[requests.Session] = None,
    ):
        self.access_token = access_token.strip()
        self.account_id = str(account_id).strip()
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent or "autoharvest (time-tracker)"
        self.session = session or requests.Session()
        self._company_config: Optional[dict[str, Any]] = None
        # Set when /company couldn't be read and defaults (duration mode, 24h clock) are in use.
        self.company_config_error: Optional[str] = None
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Harvest-Account-Id": self.account_id,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.request(method, url, params=params, json=json_data, timeout=30)
        except requests.RequestException as e:
            raise HarvestAPIError(f"Network error communicating with Harvest: {e}") from e

        if resp.status_code == 401:
            raise HarvestAPIError(
                "Harvest authentication failed (401 Unauthorized). Please check HARVEST_ACCESS_TOKEN and HARVEST_ACCOUNT_ID.",
                status_code=401,
                response_text=resp.text,
            )
        if resp.status_code == 403:
            raise HarvestAPIError(
                "Harvest authorization failed (403 Forbidden). Ensure your token has access to this account.",
                status_code=403,
                response_text=resp.text,
            )
        if resp.status_code == 422:
            raise HarvestAPIError(
                f"Harvest validation error (422 Unprocessable Entity): {resp.text}",
                status_code=422,
                response_text=resp.text,
            )
        if not resp.ok:
            raise HarvestAPIError(
                f"Harvest API error {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                response_text=resp.text,
            )

        return resp.json()

    def get_current_user(self) -> HarvestUser:
        """Fetch the authenticated user's profile from /users/me."""
        data = self._request("GET", "/users/me")
        return HarvestUser(
            id=data.get("id", 0),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            email=data.get("email", ""),
        )

    def get_company(self) -> dict[str, Any]:
        """Fetch company configuration from /company."""
        return self._request("GET", "/company")

    def get_company_config(self) -> dict[str, Any]:
        """Get cached company configuration."""
        if self._company_config is None:
            try:
                self._company_config = self.get_company()
            except Exception as e:
                # Fall back to defaults, but record why so the CLI can warn about it.
                self._company_config = {}
                self.company_config_error = str(e)
        return self._company_config

    @property
    def wants_timestamp_timers(self) -> bool:
        """Whether this Harvest account uses start/end timestamps rather than raw decimal hours."""
        return bool(self.get_company_config().get("wants_timestamp_timers", False))

    @property
    def clock(self) -> str:
        """Clock format: '24h' or '12h'."""
        return str(self.get_company_config().get("clock", "24h"))

    def format_time_for_harvest(self, dt: datetime) -> str:
        """Format a datetime into a time string expected by Harvest (12h or 24h)."""
        if self.clock == "24h":
            return dt.strftime("%H:%M")
        else:
            hour = dt.strftime("%I").lstrip("0") or "12"
            minute = dt.strftime("%M")
            am_pm = dt.strftime("%p").lower()
            return f"{hour}:{minute}{am_pm}"

    def stop_time_entry(self, entry_id: int) -> dict[str, Any]:
        """Stop an active/running timer."""
        return self._request("PATCH", f"/time_entries/{entry_id}/stop")

    def get_project_assignments(self) -> list[HarvestProject]:
        """Fetch all projects and tasks assigned to the current user."""
        data = self._request("GET", "/users/me/project_assignments")
        assignments = data.get("project_assignments", [])
        projects: list[HarvestProject] = []

        for assignment in assignments:
            proj = assignment.get("project", {})
            task_assignments = assignment.get("task_assignments", [])
            tasks: list[HarvestTask] = []

            for ta in task_assignments:
                t = ta.get("task", {})
                tasks.append(
                    HarvestTask(
                        id=t.get("id"),
                        name=t.get("name", "Unknown Task"),
                    )
                )

            projects.append(
                HarvestProject(
                    id=proj.get("id"),
                    name=proj.get("name", "Unknown Project"),
                    code=proj.get("code"),
                    tasks=tasks,
                )
            )

        projects.sort(key=lambda p: p.name.lower())
        return projects

    def get_time_entries(
        self,
        from_date: str,
        to_date: str,
        user_id: Optional[int] = None,
    ) -> list[HarvestTimeEntry]:
        """
        Fetch time entries in a date range (YYYY-MM-DD).
        Handles pagination automatically.
        """
        page = 1
        per_page = 100
        entries: list[HarvestTimeEntry] = []

        while True:
            params: dict[str, Any] = {
                "from": from_date,
                "to": to_date,
                "page": page,
                "per_page": per_page,
            }
            if user_id:
                params["user_id"] = user_id

            data = self._request("GET", "/time_entries", params=params)
            raw_entries = data.get("time_entries", [])

            for item in raw_entries:
                ext_ref = item.get("external_reference") or {}
                entries.append(
                    HarvestTimeEntry(
                        id=item.get("id"),
                        spent_date=item.get("spent_date", ""),
                        hours=float(item.get("hours", 0.0)),
                        notes=item.get("notes") or "",
                        project_id=item.get("project", {}).get("id", 0),
                        project_name=item.get("project", {}).get("name"),
                        task_id=item.get("task", {}).get("id", 0),
                        task_name=item.get("task", {}).get("name"),
                        external_reference_id=ext_ref.get("id"),
                        started_time=item.get("started_time"),
                        ended_time=item.get("ended_time"),
                        is_running=bool(item.get("is_running", False)),
                    )
                )

            total_pages = data.get("total_pages", page)
            if page >= total_pages or not raw_entries:
                break
            page += 1

        return entries

    def create_time_entry(
        self,
        project_id: int,
        task_id: int,
        spent_date: str,
        hours: float,
        notes: str,
        started_time: Optional[str] = None,
        ended_time: Optional[str] = None,
        external_reference: Optional[dict[str, str]] = None,
    ) -> HarvestTimeEntry:
        """Create a new time entry in Harvest."""
        payload: dict[str, Any] = {
            "project_id": project_id,
            "task_id": task_id,
            "spent_date": spent_date,
            "notes": notes,
        }

        # If timestamp timers are enabled and start/end times are provided,
        # we must provide both started_time and ended_time so Harvest logs
        # a completed segment rather than starting an active live timer.
        if self.wants_timestamp_timers and started_time and ended_time:
            payload["started_time"] = started_time
            payload["ended_time"] = ended_time
            payload["hours"] = round(hours, 2)
        else:
            payload["hours"] = round(hours, 2)
            if started_time:
                payload["started_time"] = started_time
            if ended_time:
                payload["ended_time"] = ended_time

        if external_reference:
            payload["external_reference"] = external_reference

        data = self._request("POST", "/time_entries", json_data=payload)

        # Safety check: if Harvest somehow created the entry as a running timer,
        # immediately stop it so it is recorded as a completed segment.
        entry_id = data.get("id")
        if data.get("is_running") and entry_id:
            try:
                data = self.stop_time_entry(entry_id)
            except Exception:
                # Leave is_running=True on the returned entry so the caller can warn about it.
                pass

        ext_ref = data.get("external_reference") or {}

        return HarvestTimeEntry(
            id=data.get("id"),
            spent_date=data.get("spent_date", spent_date),
            hours=float(data.get("hours", hours)),
            notes=data.get("notes", notes),
            project_id=data.get("project", {}).get("id", project_id),
            project_name=data.get("project", {}).get("name"),
            task_id=data.get("task", {}).get("id", task_id),
            task_name=data.get("task", {}).get("name"),
            external_reference_id=ext_ref.get("id"),
            started_time=data.get("started_time", started_time),
            ended_time=data.get("ended_time", ended_time),
            is_running=bool(data.get("is_running", False)),
        )
