import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    scenario_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        SAEnum(RunStatus), nullable=False, default=RunStatus.QUEUED, index=True
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Absolute path to the expb-executor-<scenario>-<timestamp>/ output directory
    output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Parsed K6 metrics from k6-summary.json, keyed by group name
    # {"engine_newPayload": {"avg": ..., "min": ..., "max": ...,
    #                        "med": ..., "p90": ..., "p95": ..., "p99": ...},
    #  "engine_forkchoiceUpdated": {...}}
    k6_metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Full override dict from the API request, stored for audit/replay
    overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    token_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # SHA-256 hex digest of the raw token — the raw value is never stored
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now(timezone.utc)
    )
