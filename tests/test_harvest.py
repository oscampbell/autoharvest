from autoharvest.harvest import HarvestAPIError, HarvestClient
import responses


def test_harvest_get_current_user():
    client = HarvestClient(access_token="h_token", account_id="99999")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.harvestapp.com/v2/users/me",
            json={
                "id": 555,
                "first_name": "Oliver",
                "last_name": "Campbell",
                "email": "oliver@example.com",
            },
            status=200,
        )
        user = client.get_current_user()
        assert user.id == 555
        assert user.full_name == "Oliver Campbell"
        assert user.email == "oliver@example.com"


def test_harvest_get_project_assignments():
    client = HarvestClient(access_token="h_token", account_id="99999")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.harvestapp.com/v2/users/me/project_assignments",
            json={
                "project_assignments": [
                    {
                        "project": {"id": 1001, "name": "Infrastructure & SRE", "code": "INFRA"},
                        "task_assignments": [
                            {"task": {"id": 2001, "name": "On-Call Support"}},
                            {"task": {"id": 2002, "name": "Maintenance"}},
                        ],
                    }
                ]
            },
            status=200,
        )
        projects = client.get_project_assignments()
        assert len(projects) == 1
        assert projects[0].id == 1001
        assert projects[0].name == "Infrastructure & SRE"
        assert len(projects[0].tasks) == 2
        assert projects[0].tasks[0].id == 2001
        assert projects[0].tasks[0].name == "On-Call Support"


def test_harvest_get_time_entries():
    client = HarvestClient(access_token="h_token", account_id="99999")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.harvestapp.com/v2/time_entries",
            json={
                "time_entries": [
                    {
                        "id": 777,
                        "spent_date": "2026-10-12",
                        "hours": 1.0,
                        "notes": "PagerDuty Incident #101: High CPU [PD-INC101]",
                        "project": {"id": 1001, "name": "Infrastructure"},
                        "task": {"id": 2001, "name": "On-Call Support"},
                        "external_reference": {
                            "id": "INC101",
                            "permalink": "https://pd.com/incidents/INC101",
                        },
                    }
                ],
                "total_pages": 1,
            },
            status=200,
        )
        entries = client.get_time_entries("2026-10-10", "2026-10-17")
        assert len(entries) == 1
        assert entries[0].id == 777
        assert entries[0].external_reference_id == "INC101"
        assert entries[0].hours == 1.0


def test_harvest_create_time_entry():
    client = HarvestClient(access_token="h_token", account_id="99999")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "https://api.harvestapp.com/v2/time_entries",
            json={
                "id": 888,
                "spent_date": "2026-10-13",
                "hours": 1.0,
                "notes": "PagerDuty Incident #202: Memory Leak [PD-INC202]",
                "project": {"id": 1001},
                "task": {"id": 2001},
                "external_reference": {"id": "INC202"},
            },
            status=201,
        )
        entry = client.create_time_entry(
            project_id=1001,
            task_id=2001,
            spent_date="2026-10-13",
            hours=1.0,
            notes="PagerDuty Incident #202: Memory Leak [PD-INC202]",
            external_reference={"id": "INC202"},
        )
        assert entry.id == 888
        assert entry.spent_date == "2026-10-13"
        assert entry.external_reference_id == "INC202"


def test_harvest_auth_error():
    client = HarvestClient(access_token="bad", account_id="bad")
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            "https://api.harvestapp.com/v2/users/me",
            json={"error": "unauthorized"},
            status=401,
        )
        try:
            client.get_current_user()
            assert False, "Should have raised HarvestAPIError"
        except HarvestAPIError as e:
            assert e.status_code == 401
