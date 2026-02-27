"""Tests for GET /scenarios."""


def test_list_scenarios_returns_200(client, auth_headers):
    response = client.get("/scenarios", headers=auth_headers)
    assert response.status_code == 200


def test_list_scenarios_contains_test_scenario(client, auth_headers):
    response = client.get("/scenarios", headers=auth_headers)
    data = response.json()
    names = [s["name"] for s in data["scenarios"]]
    assert "test-scenario" in names


def test_scenario_info_structure(client, auth_headers):
    response = client.get("/scenarios", headers=auth_headers)
    scenario = response.json()["scenarios"][0]
    assert "name" in scenario
    assert "client" in scenario
    assert "network" in scenario
    assert "default_duration" in scenario
    assert "default_warmup_duration" in scenario
    assert "default_delay" in scenario
    assert "default_amount" in scenario
    assert "overridable_params" in scenario


def test_scenario_overridable_params_listed(client, auth_headers):
    response = client.get("/scenarios", headers=auth_headers)
    scenario = response.json()["scenarios"][0]
    params = scenario["overridable_params"]
    assert "payloads_amount" in params
    assert "payloads_delay" in params


def test_list_scenarios_requires_auth(client):
    response = client.get("/scenarios")
    assert response.status_code in (401, 403)
