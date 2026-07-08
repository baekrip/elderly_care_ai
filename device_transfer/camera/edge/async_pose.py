from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
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


class LatestPoseInferenceWorker:
    def __init__(self, infer_fn: Callable[..., Any]) -> None:
        self._infer_fn = infer_fn
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pose-infer")
        self._future: Future[Any] | None = None
        self._context: Any = None
        self.skipped_busy_count = 0
        self.submit_count = 0
        self.result_count = 0

    def submit(self, context: Any, *args: Any, **kwargs: Any) -> bool:
        if self._future is not None and not self._future.done():
            self.skipped_busy_count += 1
            return False
        self._context = context
        self._future = self._executor.submit(self._infer_fn, *args, **kwargs)
        self.submit_count += 1
        return True

    def poll(self) -> PoseInferenceResult | None:
        if self._future is None or not self._future.done():
            return None
        future = self._future
        context = self._context
        self._future = None
        self._context = None
        self.result_count += 1
        return PoseInferenceResult(context=context, output=future.result())

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
