import io
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from expb.api.auth import verify_token
from expb.api.db.models import Run, RunStatus
from expb.api.dependencies import get_db
from expb.api.schemas.runs import (
    K6MetricGroup,
    K6Metrics,
    RunListResponse,
    RunResponse,
    RunStatusResponse,
    SubmitRunRequest,
)
from expb.api.worker import BenchmarkWorker

router = APIRouter()


def _get_worker(request: Request) -> BenchmarkWorker:
    return request.app.state.worker


def _run_to_response(run: Run) -> RunResponse:
    k6: K6Metrics | None = None
    if run.k6_metrics:
        raw = run.k6_metrics
        k6 = K6Metrics(
            engine_new_payload=(
                K6MetricGroup(**raw["engine_newPayload"])
                if raw.get("engine_newPayload")
                else None
            ),
            engine_forkchoice_updated=(
                K6MetricGroup(**raw["engine_forkchoiceUpdated"])
                if raw.get("engine_forkchoiceUpdated")
                else None
            ),
        )
    return RunResponse(
        run_id=run.run_id,
        scenario_name=run.scenario_name,
        status=run.status,
        queued_at=run.queued_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        output_dir=run.output_dir,
        error_message=run.error_message,
        k6_metrics=k6,
        overrides=run.overrides,
    )


@router.post("", response_model=RunResponse, status_code=201)
def submit_run(
    body: SubmitRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
) -> RunResponse:
    """Submit a new benchmark run to the execution queue."""
    scenarios = request.app.state.scenarios
    if body.scenario_name not in scenarios.scenarios_configs:
        raise HTTPException(
            status_code=422,
            detail=f"Scenario '{body.scenario_name}' not found in the loaded config.",
        )

    run_id = str(uuid.uuid4())
    overrides = body.model_dump(exclude={"scenario_name"})

    run = Run(
        run_id=run_id,
        scenario_name=body.scenario_name,
        status=RunStatus.QUEUED,
        overrides=overrides,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    _get_worker(request).enqueue(run_id)

    return _run_to_response(run)


@router.get("", response_model=RunListResponse)
def list_runs(
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
    status: str | None = Query(default=None, description="Filter by run status."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> RunListResponse:
    """List benchmark runs, optionally filtered by status."""
    query = db.query(Run)
    if status is not None:
        query = query.filter(Run.status == status)

    total = query.count()
    runs = (
        query.order_by(Run.queued_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return RunListResponse(
        runs=[_run_to_response(r) for r in runs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
) -> RunResponse:
    """Get details and metrics for a single run."""
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return _run_to_response(run)


@router.get("/{run_id}/status", response_model=RunStatusResponse)
def get_run_status(
    run_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
) -> RunStatusResponse:
    """Lightweight status-only check for a run. Useful for polling."""
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return RunStatusResponse(run_id=run.run_id, status=run.status)


@router.delete("/{run_id}", status_code=204)
def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
) -> None:
    """
    Cancel a queued run.

    Only runs in ``queued`` status can be cancelled. A run that is already
    executing cannot be stopped mid-flight.
    """
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if run.status != RunStatus.QUEUED:
        raise HTTPException(
            status_code=409,
            detail=f"Only queued runs can be cancelled (current status: {run.status}).",
        )
    run.status = RunStatus.CANCELLED
    run.completed_at = datetime.now(timezone.utc)
    db.commit()


@router.get("/{run_id}/download")
def download_run_output(
    run_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(verify_token),
) -> Response:
    """Download the output directory of a finished run as a ZIP archive."""
    run = db.query(Run).filter(Run.run_id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    if run.status not in (RunStatus.COMPLETED, RunStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=f"Run has not finished yet (current status: {run.status}).",
        )

    if not run.output_dir:
        raise HTTPException(
            status_code=404,
            detail="No output directory recorded for this run.",
        )

    output_path = Path(run.output_dir)
    if not output_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Output directory no longer exists on disk.",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fpath in output_path.rglob("*"):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(output_path))
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="run-{run_id}.zip"',
        },
    )
