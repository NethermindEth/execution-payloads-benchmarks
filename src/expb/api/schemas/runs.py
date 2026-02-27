from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SubmitRunRequest(BaseModel):
    scenario_name: str = Field(description="Name of the scenario defined in the config file.")
    # Execution options
    per_payload_metrics: bool = Field(
        default=False,
        description="Collect per-payload K6 metrics (high cardinality).",
    )
    print_logs: bool = Field(
        default=False,
        description="Print K6 and execution client logs to the worker console.",
    )
    # Payload parameter overrides — None means use the scenario's default
    payloads_amount: int | None = Field(
        default=None,
        ge=1,
        description="Override the number of payloads to execute.",
    )
    payloads_skip: int | None = Field(
        default=None,
        ge=0,
        description="Override the number of payloads to skip at the start.",
    )
    payloads_delay: float | None = Field(
        default=None,
        ge=0.0,
        description="Override the delay between payload requests (seconds).",
    )
    payloads_warmup: int | None = Field(
        default=None,
        ge=0,
        description="Override the number of warmup payloads (no metrics collected).",
    )


class K6MetricGroup(BaseModel):
    avg: float | None = None
    min: float | None = None
    max: float | None = None
    med: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None


class K6Metrics(BaseModel):
    engine_new_payload: K6MetricGroup | None = Field(default=None, alias="engine_newPayload")
    engine_forkchoice_updated: K6MetricGroup | None = Field(
        default=None, alias="engine_forkchoiceUpdated"
    )

    model_config = {"populate_by_name": True}


class RunResponse(BaseModel):
    run_id: str
    scenario_name: str
    status: str
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    k6_metrics: K6Metrics | None = None
    overrides: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class RunListResponse(BaseModel):
    runs: list[RunResponse]
    total: int
    page: int
    page_size: int


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
