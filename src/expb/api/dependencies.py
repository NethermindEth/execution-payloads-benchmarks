from collections.abc import Generator

from sqlalchemy.orm import Session

from expb.api.db.engine import get_session


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that provides a per-request DB session."""
    db = get_session()
    try:
        yield db
    finally:
        db.close()
