import responses

from autoharvest.harvest import HarvestClient


def test_harvest_company_config_failure_is_recorded():
    """If /company can't be read, defaults are used but the reason is kept so the CLI can warn."""
    client = HarvestClient(access_token="h_token", account_id="99999")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://api.harvestapp.com/v2/company", json={"error": "boom"}, status=500)
        assert client.wants_timestamp_timers is False
    assert client.company_config_error
