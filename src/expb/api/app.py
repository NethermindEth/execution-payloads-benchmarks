from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI

from expb.api.db.engine import init_db
from expb.api.worker import BenchmarkWorker
from expb.configs.scenarios import Scenarios


def create_app(
    config_file: Path,
    db_path: Path,
    log_level: str = "INFO",
) -> FastAPI:
    """
    FastAPI application factory.

    Creates the app, wires up the DB, loads the scenarios config, starts the
    background benchmark worker, and registers all routers.

    Parameters
    ----------
    config_file:
        Path to the expb YAML configuration file.
    db_path:
        Path to the SQLite database file.
    log_level:
        Log level string passed to the worker's structured logger.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 1. Initialise DB (creates tables, enables WAL mode)
        init_db(db_path)

        # 2. Load scenarios config and stash on app.state for routes to access
        with config_file.open() as f:
            raw = yaml.safe_load(f)
        scenarios = Scenarios(**raw)
        app.state.scenarios = scenarios
        app.state.config_file = config_file

        # 3. Start the background benchmark worker thread
        worker = BenchmarkWorker(scenarios=scenarios, log_level=log_level)
        app.state.worker = worker
        worker.start()

        yield

        # 4. Graceful shutdown: signal worker to finish current job then stop
        worker.stop()

    app = FastAPI(
        title="expb Benchmark Queue API",
        description=(
            "Queue and monitor Ethereum execution client benchmark runs. "
            "All endpoints except /health require Bearer token authentication."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    from expb.api.routes.health import router as health_router
    from expb.api.routes.runs import router as runs_router
    from expb.api.routes.scenarios import router as scenarios_router

    app.include_router(health_router)
    app.include_router(runs_router, prefix="/runs", tags=["runs"])
    app.include_router(scenarios_router, prefix="/scenarios", tags=["scenarios"])

    return app
