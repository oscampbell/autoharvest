from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests
import responses
from autoharvest.pagerduty import PagerDutyAPIError, PagerDutyClient


def test_get_current_user():
    client = PagerDutyClient(token="pd_test_tok")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.pagerduty.com/users/me",
            json={
                "user": {
                    "id": "PUSER123",
                    "name": "Oliver Campbell",
                    "email": "oliver@example.com",
                    "time_zone": "Europe/London",
                }
            },
            status=200,
        )
        user = client.get_current_user()
        assert user.id == "PUSER123"
        assert user.name == "Oliver Campbell"
        assert user.email == "oliver@example.com"
        assert user.time_zone == "Europe/London"


def test_get_oncall_shifts():
    client = PagerDutyClient(token="pd_test_tok")
    since = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc)

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.pagerduty.com/oncalls",
            json={
                "oncalls": [
                    {
                        "start": "2026-10-10T09:00:00Z",
                        "end": "2026-10-17T09:00:00Z",
                        "schedule": {
                            "id": "SCHED1",
                            "summary": "Primary On-Call",
                        },
                        "user": {"id": "PUSER123"},
                    }
                ]
            },
            status=200,
        )
        shifts = client.get_oncall_shifts("PUSER123", since, until)
        assert len(shifts) == 1
        assert shifts[0].schedule_id == "SCHED1"
        assert shifts[0].schedule_summary == "Primary On-Call"
        assert shifts[0].user_id == "PUSER123"


def test_get_acknowledged_incidents_filtering_and_dedup():
    client = PagerDutyClient(token="pd_test_tok")
    since = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 17, 0, 0, 0, tzinfo=timezone.utc)

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.pagerduty.com/log_entries",
            json={
                "log_entries": [
                    # 1. Valid ack by PUSER123
                    {
                        "id": "LOG1",
                        "type": "acknowledge_log_entry",
                        "created_at": "2026-10-12T14:30:00Z",
                        "agent": {"id": "PUSER123", "type": "user_reference"},
                        "incident": {
                            "id": "INC101",
                            "incident_number": 101,
                            "title": "High CPU on DB",
                            "service": {"summary": "Database"},
                            "urgency": "high",
                            "html_url": "https://pd.com/incidents/INC101",
                        },
                    },
                    # 2. Ack by a DIFFERENT user -> should be ignored
                    {
                        "id": "LOG2",
                        "type": "acknowledge_log_entry",
                        "created_at": "2026-10-12T15:00:00Z",
                        "agent": {"id": "OTHER_USER", "type": "user_reference"},
                        "incident": {
                            "id": "INC102",
                            "incident_number": 102,
                            "title": "Disk Full",
                        },
                    },
                    # 3. Non-ack log entry (e.g. resolve) -> should be ignored
                    {
                        "id": "LOG3",
                        "type": "resolve_log_entry",
                        "created_at": "2026-10-12T16:00:00Z",
                        "agent": {"id": "PUSER123", "type": "user_reference"},
                        "incident": {
                            "id": "INC101",
                            "incident_number": 101,
                            "title": "High CPU on DB",
                        },
                    },
                    # 4. Duplicate ack of INC101 by PUSER123 later -> should deduplicate
                    {
                        "id": "LOG4",
                        "type": "acknowledge_log_entry",
                        "created_at": "2026-10-12T14:45:00Z",
                        "agent": {"id": "PUSER123", "type": "user_reference"},
                        "incident": {
                            "id": "INC101",
                            "incident_number": 101,
                            "title": "High CPU on DB",
                            "service": {"summary": "Database"},
                            "urgency": "high",
                            "html_url": "https://pd.com/incidents/INC101",
                        },
                    },
                    # 5. Second distinct incident acked by PUSER123
                    {
                        "id": "LOG5",
                        "type": "acknowledge_log_entry",
                        "created_at": "2026-10-13T02:15:00Z",
                        "agent": {"id": "PUSER123", "type": "user_reference"},
                        "incident": {
                            "id": "INC202",
                            "incident_number": 202,
                            "title": "Memory Leak in Worker",
                            "service": {"summary": "Background Workers"},
                            "urgency": "high",
                            "html_url": "https://pd.com/incidents/INC202",
                        },
                    },
                ],
                "more": False,
            },
            status=200,
        )

        incidents = client.get_acknowledged_incidents(
            user_id="PUSER123",
            since=since,
            until=until,
            tz=ZoneInfo("UTC"),
        )

        assert len(incidents) == 2
        assert incidents[0].incident_id == "INC101"
        assert incidents[0].incident_number == 101
        assert incidents[0].spent_date == "2026-10-12"
        # Verify earliest ack timestamp was retained (14:30 rather than 14:45)
        assert incidents[0].log_entry_id == "LOG1"

        assert incidents[1].incident_id == "INC202"
        assert incidents[1].incident_number == 202
        assert incidents[1].spent_date == "2026-10-13"


def test_pagerduty_auth_failure():
    client = PagerDutyClient(token="bad_token")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.pagerduty.com/users/me",
            json={"error": {"message": "Invalid token"}},
            status=401,
        )
        try:
            client.get_current_user()
            assert False, "Should have raised PagerDutyAPIError"
        except PagerDutyAPIError as e:
            assert e.status_code == 401
            assert "401 Unauthorized" in str(e)
