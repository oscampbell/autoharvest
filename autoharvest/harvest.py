from __future__ import annotations

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
        external_reference: Optional[dict[str, str]] = None,
    ) -> HarvestTimeEntry:
        """Create a new time entry in Harvest."""
        payload: dict[str, Any] = {
            "project_id": project_id,
            "task_id": task_id,
            "spent_date": spent_date,
            "hours": round(hours, 2),
            "notes": notes,
        }
        if external_reference:
            payload["external_reference"] = external_reference

        data = self._request("POST", "/time_entries", json_data=payload)
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
        )
