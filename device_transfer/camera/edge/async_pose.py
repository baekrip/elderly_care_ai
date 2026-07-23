from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class PoseInferenceOutput:
    detections: list[Any]
    frame_for_inference: Any
    preprocess_metadata: dict[str, Any]
    latency_ms: float


@dataclass(frozen=True)
class PoseInferenceResult:
    context: Any
    output: Any


@dataclass(frozen=True)
class PoseWorkerTelemetry:
    in_flight_count: int
    pending_count: int
    queue_depth: int
    inference_submitted: int
    submit_skipped_busy: int
    overwritten: int
    inference_completed: int
    busy_submit_count: int
    coalesce_count: int
    drop_count: int
    submit_count: int
    result_count: int


class LatestPoseInferenceWorker:
    def __init__(self, infer_fn: Callable[..., Any]) -> None:
        self._infer_fn = infer_fn
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pose-infer")
        self._lock = threading.RLock()
        self._future: Future[Any] | None = None
        self._context: Any = None
        self._pending: tuple[Any, tuple[Any, ...], dict[str, Any]] | None = None
        self._completed: tuple[Any, Future[Any]] | None = None
        self._shutting_down = False
        self.skipped_busy_count = 0
        self.coalesce_count = 0
        self.drop_count = 0
        self.submit_count = 0
        self.result_count = 0

    def submit(self, context: Any, *args: Any, **kwargs: Any) -> bool:
        with self._lock:
            if self._future is not None:
                if self._pending is not None:
                    self.drop_count += 1
                self._pending = (context, args, kwargs)
                self.skipped_busy_count += 1
                self.coalesce_count += 1
                return False
            self._start_locked(context, args, kwargs)
            return True

    def _start_locked(self, context: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self._context = context
        future = self._executor.submit(self._infer_fn, *args, **kwargs)
        self._future = future
        self.submit_count += 1
        future.add_done_callback(self._on_done)

    def _on_done(self, future: Future[Any]) -> None:
        with self._lock:
            if future is not self._future:
                return
            self._completed = (self._context, future)
            self._future = None
            self._context = None
            if self._pending is not None and not self._shutting_down:
                context, args, kwargs = self._pending
                self._pending = None
                self._start_locked(context, args, kwargs)

    def poll(self) -> PoseInferenceResult | None:
        with self._lock:
            if self._completed is None:
                return None
            context, future = self._completed
            self._completed = None
            self.result_count += 1
        return PoseInferenceResult(context=context, output=future.result())

    def telemetry_snapshot(self) -> PoseWorkerTelemetry:
        with self._lock:
            pending_count = 1 if self._pending is not None else 0
            return PoseWorkerTelemetry(
                in_flight_count=1 if self._future is not None else 0,
                pending_count=pending_count,
                queue_depth=pending_count,
                inference_submitted=int(self.submit_count),
                submit_skipped_busy=int(self.skipped_busy_count),
                overwritten=int(self.drop_count),
                inference_completed=int(self.result_count),
                busy_submit_count=int(self.skipped_busy_count),
                coalesce_count=int(self.coalesce_count),
                drop_count=int(self.drop_count),
                submit_count=int(self.submit_count),
                result_count=int(self.result_count),
            )

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            self._shutting_down = True
            self._pending = None
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
