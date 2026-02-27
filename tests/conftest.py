"""
Shared pytest fixtures for the expb API test suite.
"""

import hashlib
import secrets
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from expb.api.db.engine import get_session, init_db
from expb.api.db.models import ApiToken, Run, RunStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures: file system
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


# ---------------------------------------------------------------------------
# Fixtures: scenarios config
# ---------------------------------------------------------------------------


@pytest.fixture()
def scenarios_config_file(tmp_path: Path) -> Path:
    """
    Write a minimal valid expb.yaml to a temp file and return its path.
    The payloads / fcus files are empty stubs so Pydantic's FilePath
    validators pass.
    """
    payloads = tmp_path / "payloads.jsonl"
    fcus = tmp_path / "fcus.jsonl"
    payloads.write_text("")
    fcus.write_text("")

    config = {
        "paths": {
            "work": str(tmp_path / "work"),
            "outputs": str(tmp_path / "outputs"),
        },
        "scenarios": {
            "test-scenario": {
                "client": "nethermind",
                "snapshot_source": str(tmp_path / "snapshot"),
                "payloads": str(payloads),
                "fcus": str(fcus),
                "amount": 10,
                "duration": "5m",
                "warmup_duration": "2m",
                "delay": 0.0,
            }
        },
    }

    cfg_file = tmp_path / "expb-test.yaml"
    cfg_file.write_text(yaml.dump(config))
    return cfg_file


# ---------------------------------------------------------------------------
# Fixtures: FastAPI test client
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(db_path: Path, scenarios_config_file: Path):
    """Create the FastAPI app with a fresh in-process DB (no worker started)."""
    from expb.api.app import create_app

    init_db(db_path)

    fastapi_app = create_app(
        config_file=scenarios_config_file,
        db_path=db_path,
    )
    return fastapi_app


@pytest.fixture()
def client(app) -> TestClient:
    from unittest.mock import MagicMock, patch

    mock_worker = MagicMock()
    mock_worker.enqueue = MagicMock()

    # Keep the patch active across the full lifespan (enter → yield → exit).
    with patch("expb.api.app.BenchmarkWorker", return_value=mock_worker):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# Fixtures: auth token
# ---------------------------------------------------------------------------


@pytest.fixture()
def raw_token(db_path: Path) -> str:
    """Insert an active API token into the DB and return the raw value."""
    init_db(db_path)
    db = get_session()
    raw = secrets.token_hex(32)
    token = ApiToken(
        token_id=str(uuid.uuid4()),
        name="test-token",
        token_hash=_hash_token(raw),
    )
    db.add(token)
    db.commit()
    db.close()
    return raw


@pytest.fixture()
def auth_headers(raw_token: str) -> dict:
    return {"Authorization": f"Bearer {raw_token}"}


# ---------------------------------------------------------------------------
# Fixtures: pre-existing runs
# ---------------------------------------------------------------------------


@pytest.fixture()
def queued_run(db_path: Path) -> Run:
    init_db(db_path)
    db = get_session()
    run = Run(
        run_id=str(uuid.uuid4()),
        scenario_name="test-scenario",
        status=RunStatus.QUEUED,
        overrides={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.run_id
    db.close()

    # Re-fetch in a fresh session so the object is detached/clean for test use
    db2 = get_session()
    r = db2.query(Run).filter(Run.run_id == run_id).first()
    db2.close()
    return r


@pytest.fixture()
def completed_run(db_path: Path, tmp_path: Path) -> Run:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "k6.log").write_text("log content")

    init_db(db_path)
    db = get_session()
    run = Run(
        run_id=str(uuid.uuid4()),
        scenario_name="test-scenario",
        status=RunStatus.COMPLETED,
        overrides={},
        output_dir=str(output_dir),
        k6_metrics={
            "engine_newPayload": {
                "avg": 100.0,
                "min": 50.0,
                "max": 200.0,
                "med": 95.0,
                "p90": 150.0,
                "p95": 175.0,
                "p99": 195.0,
            },
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.run_id
    db.close()

    db2 = get_session()
    r = db2.query(Run).filter(Run.run_id == run_id).first()
    db2.close()
    return r
