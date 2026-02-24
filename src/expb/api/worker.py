import queue
import threading
import traceback
from datetime import datetime, timezone

from expb.api.db.engine import get_session
from expb.api.db.models import Run, RunStatus
from expb.api.metrics import parse_k6_summary
from expb.configs.scenarios import Scenarios
from expb.logging import Logger, setup_logging
from expb.payloads import Executor, ExecutorExecuteOptions


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

            # --- Mark as RUNNING ---
            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(timezone.utc)
            db.commit()

            self._logger.info(
                "Starting run",
                run_id=run_id,
                scenario=run.scenario_name,
            )

            # --- Build executor ---
            executor = Executor.from_scenarios(
                self._scenarios,
                scenario_name=run.scenario_name,
                logger=self._logger,
            )

            # Apply overrides to executor.config (not to the shared Scenario model)
            self._apply_overrides(executor, run.overrides or {})

            # --- Execute (blocking) ---
            options = self._build_options(run.overrides or {})
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
    def _apply_overrides(executor: Executor, overrides: dict) -> None:
        """
        Apply API-provided overrides directly to ``executor.config`` attributes.

        Overrides target the ``ExecutorConfig`` plain-object fields rather than
        the shared ``Scenario`` Pydantic model, so there is no risk of polluting
        the loaded scenarios for subsequent runs.
        """
        if overrides.get("payloads_amount") is not None:
            executor.config.k6_payloads_amount = overrides["payloads_amount"]

        if overrides.get("payloads_skip") is not None:
            executor.config.k6_payloads_skip = overrides["payloads_skip"]

        if overrides.get("payloads_delay") is not None:
            executor.config.k6_payloads_delay = overrides["payloads_delay"]
            # Mirror the Pydantic model_validator: warmup_delay defaults to delay
            # unless the scenario already set them independently.
            executor.config.k6_payloads_warmup_delay = overrides["payloads_delay"]

        if overrides.get("payloads_warmup") is not None:
            executor.config.k6_payloads_warmup = overrides["payloads_warmup"]

    @staticmethod
    def _build_options(overrides: dict) -> ExecutorExecuteOptions:
        return ExecutorExecuteOptions(
            print_logs_to_console=overrides.get("print_logs", False),
            collect_per_payload_metrics=overrides.get("per_payload_metrics", False),
            # per_payload_metrics_logs prints a table to stdout — not useful in API context
            per_payload_metrics_logs=False,
        )
