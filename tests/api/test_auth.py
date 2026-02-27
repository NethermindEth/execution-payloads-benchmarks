"""Tests for Bearer token authentication."""

import hashlib

from expb.api.db.engine import get_session
from expb.api.db.models import ApiToken


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def test_missing_token_returns_403(client):
    response = client.get("/runs")
    # FastAPI HTTPBearer returns 403 when the header is missing entirely
    assert response.status_code in (401, 403)


def test_invalid_token_returns_401(client):
    response = client.get("/runs", headers={"Authorization": "Bearer invalid_token"})
    assert response.status_code == 401


def test_valid_token_is_accepted(client, auth_headers):
    response = client.get("/runs", headers=auth_headers)
    assert response.status_code == 200


def test_valid_token_updates_last_used_at(client, db_path, auth_headers, raw_token):
    """Successful auth should stamp last_used_at on the token row."""
    # Before the request, last_used_at should be None
    db = get_session()
    token = db.query(ApiToken).filter(ApiToken.name == "test-token").first()
    assert token is not None
    assert token.last_used_at is None
    db.close()

    client.get("/runs", headers=auth_headers)

    db = get_session()
    token = db.query(ApiToken).filter(ApiToken.name == "test-token").first()
    assert token is not None
    assert token.last_used_at is not None
    db.close()


def test_revoked_token_returns_401(client, db_path, raw_token):
    """After deleting a token, requests using it should be rejected."""
    db = get_session()
    token = db.query(ApiToken).filter(ApiToken.name == "test-token").first()
    db.delete(token)
    db.commit()
    db.close()

    response = client.get("/runs", headers={"Authorization": f"Bearer {raw_token}"})
    assert response.status_code == 401
