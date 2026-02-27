import queue
import threading
import traceback
from datetime import datetime, timezone

from expb.api.db.engine import get_session
from expb.api.db.models import Run, RunStatus
from expb.api.metrics import parse_k6_summary
from expb.api.schemas.runs import ScenarioOverrides
from expb.configs.scenarios import Scenario, Scenarios
from expb.logging import Logger, setup_logging
from expb.payloads import Executor, ExecutorConfig, ExecutorExecuteOptions
from expb.payloads.executor.services.snapshots import setup_snapshot_service


class BenchmarkWorker:
    """
    Single background daemon thread that consumes ``run_id`` strings from an
    in-memory queue and executes benchmark scenarios one at a time.

    Thread safety notes
    -------------------
    * The ``Scenarios`` object is read-only after server startup — no locking needed.
    * Each call to ``_process_run`` opens and closes its own DB session so it
      never shares a connection with the FastAPI request threads.
    * The ``queue.Queue`` is thread-safe by design.
    """

    def __init__(
        self,
        scenarios: Scenarios,
        log_level: str = "INFO",
    ) -> None:
        self._scenarios = scenarios
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._logger: Logger = setup_logging(log_level)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background worker thread and recover any orphaned runs."""
        self._recover_orphaned_runs()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="expb-benchmark-worker",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("Benchmark worker started")

    def stop(self) -> None:
        """Signal the worker to stop after the current job completes."""
        self._stop_event.set()
        self._queue.put(None)  # Unblock queue.get()
        if self._thread:
            self._thread.join(timeout=5)
        self._logger.info("Benchmark worker stopped")

    def enqueue(self, run_id: str) -> None:
        """Add a run_id to the execution queue."""
        self._queue.put(run_id)
        self._logger.info("Run enqueued", run_id=run_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _recover_orphaned_runs(self) -> None:
        """
        Mark any runs left in QUEUED or RUNNING state (from a previous server
        instance) as FAILED so they don't silently disappear.
        """
        db = get_session()
        try:
            orphaned = (
                db.query(Run)
                .filter(Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]))
                .all()
            )
            if orphaned:
                for run in orphaned:
                    run.status = RunStatus.FAILED
                    run.completed_at = datetime.now(timezone.utc)
                    run.error_message = "Run was interrupted by a server restart."
                db.commit()
                self._logger.warning(
                    "Marked orphaned runs as failed",
                    count=len(orphaned),
                )
        finally:
            db.close()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                run_id = self._queue.get(block=True, timeout=1.0)
            except queue.Empty:
                continue

            if run_id is None:  # Shutdown sentinel
                break

            self._process_run(run_id)

    def _process_run(self, run_id: str) -> None:
        db = get_session()
        try:
            run = db.query(Run).filter(Run.run_id == run_id).first()
            if run is None:
                self._logger.error("Run not found in DB", run_id=run_id)
                return

            # Guard against runs cancelled via the API while still in the queue.
            if run.status == RunStatus.CANCELLED:
                self._logger.info("Skipping cancelled run", run_id=run_id)
                return

            # --- Mark as RUNNING ---
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(timezone.utc)
            db.commit()

            self._logger.info(
                "Starting run",
                run_id=run_id,
                scenario=run.scenario_name,
            )

            # --- Build scenario (with overrides applied) ---
            base_scenario = self._scenarios.scenarios_configs[run.scenario_name]
            stored_overrides = run.overrides or {}
            scenario_overrides_data = stored_overrides.get("overrides") or {}
            scenario_overrides = ScenarioOverrides.model_validate(scenario_overrides_data)
            scenario = self._apply_overrides_to_scenario(base_scenario, scenario_overrides)

            # --- Build executor from the (possibly modified) scenario ---
            # This mirrors Executor.from_scenarios but uses our derived scenario so
            # ExecutorConfig performs all its own construction logic correctly.
            snapshot_service = setup_snapshot_service(self._scenarios, scenario)
            executor = Executor(
                config=ExecutorConfig(
                    scenario=scenario,
                    snapshot_service=snapshot_service,
                    paths=self._scenarios.paths,
                    resources=self._scenarios.resources,
                    pull_images=self._scenarios.pull_images,
                    docker_images=self._scenarios.docker_images,
                    exports=self._scenarios.exports,
                ),
                logger=self._logger,
            )

            # --- Execute (blocking) ---
            options = self._build_options(stored_overrides)
            executor.execute_scenario(options=options)

            # --- Capture outputs ---
            output_dir = str(executor.config.outputs_dir)
            k6_summary_path = executor.config.outputs_dir / "k6-summary.json"
            k6_metrics = parse_k6_summary(k6_summary_path)

            # --- Mark as COMPLETED ---
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            run.output_dir = output_dir
            run.k6_metrics = k6_metrics
            db.commit()

            self._logger.info(
                "Run completed",
                run_id=run_id,
                output_dir=output_dir,
            )

        except Exception as exc:
            self._logger.error("Run failed", run_id=run_id, error=str(exc))
            try:
                # Re-fetch run in case the session state is stale after the exception
                run = db.query(Run).filter(Run.run_id == run_id).first()
                if run:
                    run.status = RunStatus.FAILED
                    run.completed_at = datetime.now(timezone.utc)
                    run.error_message = (
                        f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
                    )
                    db.commit()
            except Exception as db_exc:
                self._logger.error(
                    "Failed to persist run failure to DB",
                    run_id=run_id,
                    error=str(db_exc),
                )
        finally:
            db.close()

    @staticmethod
    def _apply_overrides_to_scenario(base: Scenario, overrides: ScenarioOverrides) -> Scenario:
        """
        Return a new ``Scenario`` with the given overrides applied.

        Serialises the base scenario to an alias-keyed JSON dict, merges the
        non-``None`` override values (whose field names intentionally match the
        Scenario aliases), then reconstructs via ``Scenario.model_validate`` so
        all validators — including the ``payloads_warmup_delay`` defaulting logic
        — run on the final result.

        The shared base scenario object is never mutated.
        """
        # Produce an alias-keyed, JSON-serialisable snapshot of the base scenario.
        # model_validate accepts this format, and all Scenario aliases are used as keys.
        data = base.model_dump(by_alias=True, mode="json")

        # ScenarioOverrides fields are named to match the corresponding Scenario aliases,
        # so a direct dict.update() is sufficient to apply them.
        data.update(overrides.model_dump(mode="json", exclude_none=True))

        return Scenario.model_validate(data)

    @staticmethod
    def _build_options(stored_overrides: dict) -> ExecutorExecuteOptions:
        return ExecutorExecuteOptions(
            print_logs_to_console=stored_overrides.get("print_logs", False),
            collect_per_payload_metrics=stored_overrides.get("per_payload_metrics", False),
            # per_payload_metrics_logs prints a table to stdout — not useful in API context
            per_payload_metrics_logs=False,
        )
