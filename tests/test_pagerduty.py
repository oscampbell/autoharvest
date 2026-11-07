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
            chunk_days=7.0,
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


def test_get_acknowledged_incidents_chunking():
    """Verify that multi-day queries are chunked cleanly without exceeding offset limits."""
    client = PagerDutyClient(token="pd_test_tok")
    since = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)

    with responses.RequestsMock() as rsps:
        # Day 1: 10-10 to 10-11
        rsps.add(
            responses.GET,
            "https://api.pagerduty.com/log_entries",
            json={
                "log_entries": [
                    {
                        "id": "LOG1",
                        "type": "acknowledge_log_entry",
                        "created_at": "2026-10-10T12:00:00Z",
                        "agent": {"id": "PUSER123", "type": "user_reference"},
                        "incident": {
                            "id": "INC1",
                            "incident_number": 1,
                            "title": "Alert Day 1",
                        },
                    }
                ],
                "more": False,
            },
            status=200,
        )
        # Day 2: 10-11 to 10-12
        rsps.add(
            responses.GET,
            "https://api.pagerduty.com/log_entries",
            json={
                "log_entries": [
                    {
                        "id": "LOG2",
                        "type": "acknowledge_log_entry",
                        "created_at": "2026-10-11T12:00:00Z",
                        "agent": {"id": "PUSER123", "type": "user_reference"},
                        "incident": {
                            "id": "INC2",
                            "incident_number": 2,
                            "title": "Alert Day 2",
                        },
                    }
                ],
                "more": False,
            },
            status=200,
        )

        progress_calls = []
        def track_progress(start, end, count):
            progress_calls.append((start, end, count))

        incidents = client.get_acknowledged_incidents(
            user_id="PUSER123",
            since=since,
            until=until,
            tz=ZoneInfo("UTC"),
            chunk_days=1.0,
            progress_callback=track_progress,
        )

        assert len(incidents) == 2
        assert len(progress_calls) == 2
        assert incidents[0].incident_id == "INC1"
        assert incidents[1].incident_id == "INC2"


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


def _ack_entry(entry_id, inc_id, inc_num, created_at):
    return {
        "id": entry_id,
        "type": "acknowledge_log_entry",
        "created_at": created_at,
        "agent": {"id": "PUSER123"},
        "incident": {"id": inc_id, "incident_number": inc_num, "created_at": created_at},
    }


def test_get_acknowledged_incidents_splits_window_over_offset_cap(monkeypatch):
    """A day with more log entries than the pagination cap is split in half, not truncated."""
    import json
    from urllib.parse import parse_qs, urlparse
    import autoharvest.pagerduty as pd_module

    monkeypatch.setattr(pd_module, "PAGERDUTY_MAX_OFFSET", 200)  # keep the test small
    client = PagerDutyClient(token="pd_test_tok")
    since = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 11, 0, 0, 0, tzinfo=timezone.utc)
    seen = []

    def callback(request):
        q = parse_qs(urlparse(request.url).query)
        start = datetime.fromisoformat(q["since"][0])
        end = datetime.fromisoformat(q["until"][0])
        offset, limit = int(q["offset"][0]), int(q["limit"][0])
        seen.append((start, end, offset))
        assert offset + limit <= 200, "must never request past the cap"
        if end - start == until - since:
            # The full day is too dense: every page is full and there is always more.
            body = {"log_entries": [{"id": f"N{offset + i}", "type": "notify_log_entry"} for i in range(100)], "more": True}
        elif start == since:
            body = {"log_entries": [_ack_entry("L1", "PINC1", 101, "2026-10-10T03:00:00Z")], "more": False}
        else:
            body = {"log_entries": [_ack_entry("L2", "PINC2", 102, "2026-10-10T20:00:00Z")], "more": False}
        return (200, {}, json.dumps(body))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(responses.GET, "https://api.pagerduty.com/log_entries", callback=callback)
        incidents = client.get_acknowledged_incidents(user_id="PUSER123", since=since, until=until)

    assert [i.incident_id for i in incidents] == ["PINC1", "PINC2"]
    halves = {(s, e) for s, e, _ in seen if e - s < until - since}
    assert halves == {(since, datetime(2026, 10, 10, 12, tzinfo=timezone.utc)), (datetime(2026, 10, 10, 12, tzinfo=timezone.utc), until)}


def test_get_acknowledged_incidents_raises_when_window_cannot_be_split_further(monkeypatch):
    """If even a one-minute window exceeds the cap, fail loudly rather than drop alerts."""
    import autoharvest.pagerduty as pd_module

    monkeypatch.setattr(pd_module, "PAGERDUTY_MAX_OFFSET", 200)
    client = PagerDutyClient(token="pd_test_tok")
    since = datetime(2026, 10, 10, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 10, 0, 4, 0, tzinfo=timezone.utc)
    full_page = {
        "log_entries": [{"id": f"L{i}", "type": "notify_log_entry"} for i in range(100)],
        "more": True,
    }

    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://api.pagerduty.com/log_entries", json=full_page, status=200)
        try:
            client.get_acknowledged_incidents(user_id="PUSER123", since=since, until=until)
            assert False, "Should have raised PagerDutyAPIError"
        except PagerDutyAPIError as e:
            assert "incomplete" in str(e)
