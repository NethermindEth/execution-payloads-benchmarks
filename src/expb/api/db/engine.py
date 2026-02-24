from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_SessionLocal = None


def init_db(db_path: Path) -> None:
    global _engine, _SessionLocal

    _engine = create_engine(
        f"sqlite:///{db_path}",
        # Required: SQLite connections may be used from multiple threads
        # (FastAPI request threads + the background worker thread).
        connect_args={"check_same_thread": False},
    )

    # Enable WAL journal mode so that concurrent reads from FastAPI handlers
    # do not block the worker's writes, and vice versa.
    with _engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    from expb.api.db.models import Base

    Base.metadata.create_all(_engine)


def get_engine():
    if _engine is None:
        raise RuntimeError("Database has not been initialised. Call init_db() first.")
    return _engine


def get_session() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("Database has not been initialised. Call init_db() first.")
    return _SessionLocal()
