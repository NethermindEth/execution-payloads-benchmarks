from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from expb.configs.scenarios import ScenarioExtraVolume


class ScenarioOverrides(BaseModel):
    """
    Optional per-run overrides for a scenario's configuration.

    All fields default to ``None``, meaning the base scenario's configured
    value is used unchanged.

    The following scenario fields are intentionally NOT overridable via the
    API: ``name``, ``payloads``, ``fcus``, ``network``, ``snapshot_source``,
    ``snapshot_backend``, ``snapshot_path``.
    """

    # --- Client ---
    client: str | None = Field(
        default=None,
        description="Override the execution client (e.g. 'nethermind', 'geth').",
    )
    image: str | None = Field(
        default=None,
        description="Override the execution client Docker image.",
    )
    # --- Payload parameters ---
    repeat: int | None = Field(
        default=None,
        ge=1,
        description="Override the number of times to repeat the scenario.",
    )
    amount: int | None = Field(
        default=None,
        ge=1,
        description="Override the number of payloads to execute.",
    )
    skip: int | None = Field(
        default=None,
        ge=0,
        description="Override the number of payloads to skip at the start.",
    )
    warmup: int | None = Field(
        default=None,
        ge=0,
        description="Override the number of warmup payloads (no metrics collected).",
    )
    delay: float | None = Field(
        default=None,
        ge=0.0,
        description="Override the delay between payload requests (seconds).",
    )
    warmup_delay: float | None = Field(
        default=None,
        ge=0.0,
        description="Override the delay between warmup payload requests (seconds).",
    )
    # --- Timing ---
    duration: str | None = Field(
        default=None,
        description="Override the max scenario duration (e.g. '10m').",
    )
    warmup_duration: str | None = Field(
        default=None,
        description="Override the max warmup phase duration (e.g. '5m').",
    )
    startup_wait: int | None = Field(
        default=None,
        ge=0,
        description="Override the client startup wait time (seconds).",
    )
    warmup_wait: int | None = Field(
        default=None,
        ge=0,
        description="Override the wait between warmup and benchmark payloads (seconds).",
    )
    # --- Execution client configuration ---
    extra_flags: list[str] | None = Field(
        default=None,
        description="Override extra CLI flags passed to the execution client.",
    )
    extra_env: dict[str, str] | None = Field(
        default=None,
        description="Override extra environment variables for the execution client.",
    )
    extra_commands: list[str] | None = Field(
        default=None,
        description="Override extra commands run inside the execution client container.",
    )
    extra_volumes: dict[str, ScenarioExtraVolume] | None = Field(
        default=None,
        description="Override extra volume mounts for the execution client container.",
    )


class SubmitRunRequest(BaseModel):
    scenario_name: str = Field(description="Name of the scenario defined in the config file.")
    # Execution options — server-side behaviour, not scenario configuration
    per_payload_metrics: bool = Field(
        default=False,
        description="Collect per-payload K6 metrics (high cardinality).",
    )
    print_logs: bool = Field(
        default=False,
        description="Print K6 and execution client logs to the worker console.",
    )
    # Optional scenario overrides
    overrides: ScenarioOverrides | None = Field(
        default=None,
        description="Optional overrides for the base scenario configuration.",
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
