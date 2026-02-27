from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from expb.api.db.models import Run, RunStatus
from expb.api.dependencies import get_db

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str
    queue_size: int
    active_jobs: int
    database_connected: bool


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health_check(
    request: Request,
    db: Session = Depends(get_db),
) -> HealthResponse:
    """
    Health check endpoint. No authentication required.

    Returns overall API status, DB connectivity, and queue information
    derived live from the database.
    """
    database_connected = False
    try:
        db.execute(text("SELECT 1"))
        database_connected = True
    except Exception:
        pass

    queue_size = 0
    active_jobs = 0
    if database_connected:
        queue_size = db.query(Run).filter(Run.status == RunStatus.QUEUED).count()
        active_jobs = db.query(Run).filter(Run.status == RunStatus.RUNNING).count()

    return HealthResponse(
        status="ok" if database_connected else "degraded",
        version=request.app.version,
        queue_size=queue_size,
        active_jobs=active_jobs,
        database_connected=database_connected,
    )
