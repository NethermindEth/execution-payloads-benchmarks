import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from expb.api.dependencies import get_db
from expb.api.db.models import ApiToken

_bearer_scheme = HTTPBearer(auto_error=True)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme),
    db: Session = Depends(get_db),
) -> None:
    """
    FastAPI dependency that validates a Bearer token against the DB.

    On success, updates the token's ``last_used_at`` timestamp.
    Raises HTTP 403 if the ``Authorization`` header is missing (FastAPI default
    for ``HTTPBearer``). Raises HTTP 401 if the token is invalid or revoked.
    Use as: ``_: None = Depends(verify_token)``
    """
    computed_hash = _hash_token(credentials.credentials)

    # Query directly by the indexed hash column — no need to scan all tokens.
    token = db.query(ApiToken).filter(ApiToken.token_hash == computed_hash).first()
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token.")

    token.last_used_at = datetime.now(timezone.utc)
    db.commit()
