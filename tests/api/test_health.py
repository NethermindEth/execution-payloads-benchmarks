"""Tests for GET /health."""


def test_health_no_auth_required(client):
    """Health endpoint should not require a Bearer token."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_structure(client):
    response = client.get("/health")
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "queue_size" in data
    assert "active_jobs" in data
    assert "database_connected" in data


def test_health_database_connected(client):
    response = client.get("/health")
    data = response.json()
    assert data["database_connected"] is True
    assert data["status"] == "ok"


def test_health_queue_counts_reflect_db(client, auth_headers, queued_run):
    """queue_size should reflect runs in QUEUED status."""
    response = client.get("/health")
    data = response.json()
    assert data["queue_size"] >= 1
    assert data["active_jobs"] == 0
