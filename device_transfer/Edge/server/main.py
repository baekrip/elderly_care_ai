from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI

from server.api.activity import router as activity_router
from server.api.candidates import router as candidates_router
from server.api.clips import router as clips_router
from server.api.events import router as events_router
from server.api.health import router as health_router
from server.api.overlay_ws import router as overlay_ws_router
from server.api.skeleton_ws import router as skeleton_ws_router
from server.api.stream import router as stream_router
from server.config import load_config
from server.db.database import create_tables, init_db
from server.services.edge_hub import EdgeHub
from server.services.pattern_analyzer import PatternAnalyzer
from server.services.overlay_broadcaster import OverlayBroadcaster
from server.services.pi5_pipeline import Pi5SkeletonPipeline
from server.services.backend_forwarder import (
    BackendEventBatchScheduler,
    BackendForwarder,
    config_from_project_config,
)
from server.services.stgcn_classifier import STGCNClassifier
from server.security.ingest_auth import IngestAuthMiddleware
from server.result_archive import ResultArchive

def _load_project_dotenv() -> None:
    project_root = Path(__file__).resolve().parents[1]
    dotenv_path = project_root / ".env"

    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()

        if key.startswith("export "):
            key = key[len("export "):].strip()

        if not key:
            continue

        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)

def _pattern_snapshot_config(config: dict) -> dict:
    return (config.get("pattern_analyzer", {}) or {}).get("snapshot", {}) or {}


def restore_pattern_snapshot(config: dict, analyzer: PatternAnalyzer) -> dict:
    snapshot_cfg = _pattern_snapshot_config(config)
    if not bool(snapshot_cfg.get("enabled", False)):
        return {"status": "disabled"}
    return analyzer.restore_snapshot(
        snapshot_cfg.get("path", "server/storage/results/pattern_state_snapshot.json"),
        max_gap_sec=int(snapshot_cfg.get("max_gap_sec", 300)),
    )


def save_pattern_snapshot(config: dict, analyzer: PatternAnalyzer) -> None:
    snapshot_cfg = _pattern_snapshot_config(config)
    if not bool(snapshot_cfg.get("enabled", False)):
        return
    analyzer.save_snapshot(
        snapshot_cfg.get("path", "server/storage/results/pattern_state_snapshot.json"),
        camera_id=str(config.get("camera", {}).get("camera_id", "raspi_cam01")),
        window_start_ts=0,
        window_end_ts=0,
        max_snapshot_size_kb=int(snapshot_cfg.get("max_snapshot_size_kb", 0) or 0) or None,
    )


def flush_batcher_if_pending(batcher: Any) -> Any | None:
    if not getattr(batcher, "_events", []):
        return None
    return batcher.flush()


async def run_backend_batcher(batcher: Any) -> None:
    interval = max(min(float(getattr(batcher, "interval_sec", 1.0)), 1.0), 0.1)
    while True:
        await anyio.sleep(interval)
        flush_batcher_if_pending(batcher)


def _resolve_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists() or path.is_absolute():
        return path
    package_root = Path(__file__).resolve().parents[1]
    candidate = package_root / config_path
    if candidate.exists():
        return candidate
    return path


def create_app(config_path: str = "server/config.yaml") -> FastAPI:
    _load_project_dotenv()
    config = load_config(_resolve_config_path(config_path))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(config["database"]["url"])
        app.state.config = config
        app.state.edge_hub = EdgeHub()
        app.state.result_archive = ResultArchive(config)
        app.state.pattern_analyzer = PatternAnalyzer(config.get("pattern_analyzer", {}))
        app.state.pattern_snapshot_restore = restore_pattern_snapshot(config, app.state.pattern_analyzer)
        app.state.overlay_broadcaster = OverlayBroadcaster()
        backend_cfg = config_from_project_config(config)
        app.state.backend_event_batcher = BackendEventBatchScheduler(
            BackendForwarder(backend_cfg),
            interval_sec=backend_cfg.batch_interval_sec,
        )
        app.state.backend_normal_batcher = BackendEventBatchScheduler(
            BackendForwarder(backend_cfg),
            interval_sec=backend_cfg.normal_batch_interval_sec,
        )
        stgcn_cfg = config.get("stgcn", {})
        app.state.stgcn_classifier = STGCNClassifier(
            model_path=stgcn_cfg.get("model_path"),
            device=stgcn_cfg.get("device", "cpu"),
            backend=stgcn_cfg.get("backend", "pytorch"),
        )
        app.state.pi5_pipeline = Pi5SkeletonPipeline(
            config,
            archive=app.state.result_archive,
            stgcn_classifier=app.state.stgcn_classifier,
        )
        await create_tables()
        try:
            async with anyio.create_task_group() as task_group:
                if backend_cfg.enabled:
                    task_group.start_soon(run_backend_batcher, app.state.backend_event_batcher)
                    task_group.start_soon(run_backend_batcher, app.state.backend_normal_batcher)
                try:
                    yield
                finally:
                    task_group.cancel_scope.cancel()
                    flush_batcher_if_pending(app.state.backend_event_batcher)
                    flush_batcher_if_pending(app.state.backend_normal_batcher)
        finally:
            save_pattern_snapshot(config, app.state.pattern_analyzer)

    app = FastAPI(title="Elderly Care AI Server", lifespan=lifespan)
    app.add_middleware(IngestAuthMiddleware, config=config)
    app.include_router(health_router)
    app.include_router(activity_router)
    app.include_router(candidates_router)
    app.include_router(clips_router)
    app.include_router(events_router)
    app.include_router(overlay_ws_router)
    app.include_router(skeleton_ws_router)
    app.include_router(stream_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the FastAPI server")
    parser.add_argument("--config", default="server/config.yaml")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(args.config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
