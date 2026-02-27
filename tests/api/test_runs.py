"""Tests for /runs endpoints."""

import uuid

from expb.api.db.engine import get_session
from expb.api.db.models import Run, RunStatus

# ---------------------------------------------------------------------------
# POST /runs
# ---------------------------------------------------------------------------


def test_submit_run_returns_201(client, auth_headers):
    response = client.post(
        "/runs",
        json={"scenario_name": "test-scenario"},
        headers=auth_headers,
    )
    assert response.status_code == 201


def test_submit_run_response_structure(client, auth_headers):
    response = client.post(
        "/runs",
        json={"scenario_name": "test-scenario"},
        headers=auth_headers,
    )
    data = response.json()
    assert "run_id" in data
    assert data["scenario_name"] == "test-scenario"
    assert data["status"] == "queued"
    assert data["queued_at"] is not None
    assert data["started_at"] is None
    assert data["completed_at"] is None


def test_submit_run_enqueues_to_worker(client, auth_headers, app):
    client.post(
        "/runs",
        json={"scenario_name": "test-scenario"},
        headers=auth_headers,
    )
    app.state.worker.enqueue.assert_called_once()


def test_submit_run_unknown_scenario_returns_422(client, auth_headers):
    response = client.post(
        "/runs",
        json={"scenario_name": "nonexistent"},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_submit_run_with_overrides(client, auth_headers):
    response = client.post(
        "/runs",
        json={
            "scenario_name": "test-scenario",
            "overrides": {
                "amount": 5,
                "delay": 0.5,
            },
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["overrides"]["overrides"]["amount"] == 5
    assert data["overrides"]["overrides"]["delay"] == 0.5


# ---------------------------------------------------------------------------
# GET /runs
# ---------------------------------------------------------------------------


def test_list_runs_empty(client, auth_headers):
    response = client.get("/runs", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["runs"] == []
    assert data["total"] == 0


def test_list_runs_returns_submitted(client, auth_headers, queued_run):
    response = client.get("/runs", headers=auth_headers)
    data = response.json()
    assert data["total"] >= 1
    ids = [r["run_id"] for r in data["runs"]]
    assert queued_run.run_id in ids


def test_list_runs_filter_by_status(client, auth_headers, queued_run, completed_run):
    response = client.get("/runs?status=queued", headers=auth_headers)
    data = response.json()
    statuses = {r["status"] for r in data["runs"]}
    assert statuses == {"queued"}


def test_list_runs_pagination(client, auth_headers):
    # Submit 3 runs
    for _ in range(3):
        client.post(
            "/runs", json={"scenario_name": "test-scenario"}, headers=auth_headers
        )

    resp1 = client.get("/runs?page=1&page_size=2", headers=auth_headers)
    resp2 = client.get("/runs?page=2&page_size=2", headers=auth_headers)
    assert len(resp1.json()["runs"]) == 2
    assert len(resp2.json()["runs"]) >= 1


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------


def test_get_run_returns_200(client, auth_headers, queued_run):
    response = client.get(f"/runs/{queued_run.run_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["run_id"] == queued_run.run_id


def test_get_run_unknown_returns_404(client, auth_headers):
    response = client.get(f"/runs/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


def test_get_run_includes_k6_metrics(client, auth_headers, completed_run):
    response = client.get(f"/runs/{completed_run.run_id}", headers=auth_headers)
    data = response.json()
    assert data["k6_metrics"] is not None
    enp = data["k6_metrics"]["engine_newPayload"]
    assert enp["avg"] == 100.0
    assert enp["p99"] == 195.0


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/status
# ---------------------------------------------------------------------------


def test_get_run_status(client, auth_headers, queued_run):
    response = client.get(f"/runs/{queued_run.run_id}/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == queued_run.run_id
    assert data["status"] == "queued"


def test_get_run_status_unknown_returns_404(client, auth_headers):
    response = client.get(f"/runs/{uuid.uuid4()}/status", headers=auth_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /runs/{run_id}  (cancel)
# ---------------------------------------------------------------------------


def test_cancel_queued_run_returns_204(client, auth_headers, queued_run):
    response = client.delete(f"/runs/{queued_run.run_id}", headers=auth_headers)
    assert response.status_code == 204


def test_cancel_queued_run_sets_cancelled_status(
    client, db_path, auth_headers, queued_run
):
    client.delete(f"/runs/{queued_run.run_id}", headers=auth_headers)
    db = get_session()
    run = db.query(Run).filter(Run.run_id == queued_run.run_id).first()
    assert run is not None
    assert run.status == RunStatus.CANCELLED
    assert run.completed_at is not None
    db.close()


def test_cancel_non_queued_run_returns_409(client, auth_headers, completed_run):
    response = client.delete(f"/runs/{completed_run.run_id}", headers=auth_headers)
    assert response.status_code == 409


def test_cancel_unknown_run_returns_404(client, auth_headers):
    response = client.delete(f"/runs/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /runs/{run_id}/download
# ---------------------------------------------------------------------------


def test_download_completed_run(client, auth_headers, completed_run):
    response = client.get(
        f"/runs/{completed_run.run_id}/download", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


def test_download_queued_run_returns_409(client, auth_headers, queued_run):
    response = client.get(f"/runs/{queued_run.run_id}/download", headers=auth_headers)
    assert response.status_code == 409
