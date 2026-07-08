# YOLO 추론 병목 진단 및 해상도 분리 — TDD 구현 계획

> 기준: Pi5 현재 상태 — `yolo26s-pose.onnx`, `imgsz=320`, `inference FPS ~5`, `capture 640×360 30 FPS`  
> 목표: RTSP 720p~1080p 유지 + YOLO 입력 320~640 분리 + 좌표 역변환 + skeleton JSON 스케일 좌표 포함  
> 방식: 각 단계를 테스트 먼저 작성 → 구현 → 검증 순서로 진행

---

## 목차

1. [현재 상태 진단](#1-현재-상태-진단)
2. [병목 원인 분류 및 측정 계획](#2-병목-원인-분류-및-측정-계획)
3. [Phase 1 — 병목 측정 도구 구현 (TDD)](#3-phase-1--병목-측정-도구-구현-tdd)
4. [Phase 2 — ONNX 추론 최적화 (TDD)](#4-phase-2--onnx-추론-최적화-tdd)
5. [Phase 3 — 해상도 분리 파이프라인 (TDD)](#5-phase-3--해상도-분리-파이프라인-tdd)
6. [Phase 4 — 좌표 역변환 및 JSON 스케일링 (TDD)](#6-phase-4--좌표-역변환-및-json-스케일링-tdd)
7. [Phase 5 — 캡처·추론 스레드 분리 (TDD)](#7-phase-5--캡처추론-스레드-분리-tdd)
8. [Phase 6 — 통합 검증](#8-phase-6--통합-검증)
9. [수정 파일 목록 및 완료 기준](#9-수정-파일-목록-및-완료-기준)

---

## 1. 현재 상태 진단

### 1.1 현재 측정값

| 항목 | 수치 | 목표 |
|------|------|------|
| 캡처 해상도 | 640×360 | **720p(1280×720) ~ 1080p** |
| RTSP FPS | 30.0 FPS ✅ | 30 FPS 유지 |
| inference FPS | **4.9~5.0 FPS** ❌ | ≥ 10 FPS |
| pose latency p95 | **184 ms** | ≤ 100 ms |
| loop latency p95 | 47 ms ✅ | — |
| YOLO 입력 | 320 imgsz | 320 유지 or 더 작게 |
| drop rate | 0.0 ✅ | 0.0 유지 |

### 1.2 문제 핵심

현재 캡처 해상도(640×360)와 YOLO 입력 해상도(320)가 **같은 파이프라인**에 묶여 있다.
720p~1080p로 캡처 해상도를 올리면 RTSP 품질이 개선되지만, 동시에 YOLO 전처리
(resize + normalize) 비용이 증가해 추론 FPS가 더 낮아지는 구조다.

```
현재 파이프라인 (문제 있는 구조):
  캡처(640×360) ──→ resize(320×320) ──→ YOLO ──→ skeleton
                └──→ RTSP pipe (640×360)

목표 파이프라인 (분리 구조):
  캡처(1280×720) ──→ RTSP pipe (1280×720, 720p 원본)
                └──→ resize(320×320) ──→ YOLO ──→ skeleton
                                                 ──→ 좌표 역변환(720p 기준)
                                                 ──→ JSON 전송
```

### 1.3 5 FPS의 가능한 원인 목록

아래 원인들이 복합적으로 작용하고 있을 가능성이 높다.
Phase 1에서 각 구간 시간을 측정해 실제 원인을 특정한다.

| 원인 | 예상 기여 | 측정 방법 |
|------|----------|----------|
| ONNX `intra_op_num_threads` 미설정 (기본값 1) | 높음 | 스레드 설정 전후 비교 |
| 캡처-추론 동일 스레드 순차 실행 | 높음 | 각 구간 타임스탬프 |
| 전처리 resize가 추론 루프 내부 포함 | 중간 | preprocess 단독 시간 |
| `graph_optimization_level` 미설정 | 낮음 | 옵션 전후 비교 |
| `inference_stride=3` 이 실제로 작동 안 하는 경우 | 낮음 | stride 로그 확인 |

---

## 2. 병목 원인 분류 및 측정 계획

### 2.1 측정 목표 — 각 단계를 독립적으로 분리

```
[T1] 캡처 시간         → camera.capture_array() 단독
[T2] resize 시간       → cv2.resize() 단독
[T3] normalize 시간    → astype + transpose 단독
[T4] ONNX 추론 시간    → session.run() 단독
[T5] 후처리 시간       → NMS + keypoint parse 단독
[T6] 전체 루프 시간    → T1 + T2 + T3 + T4 + T5 합계
```

목표는 T4(순수 추론)가 병목인지, T1(캡처 블로킹)이 병목인지 확인하는 것.

### 2.2 예상 측정 결과별 처리 방향

| T4 (추론) | T1 (캡처) | 진단 | 처리 |
|-----------|-----------|------|------|
| ≥ 150 ms | 어느 값이든 | 추론 자체가 병목 | Phase 2 (ONNX 최적화) |
| ≤ 80 ms | ≥ 50 ms | 캡처가 추론 루프 블로킹 | Phase 5 (스레드 분리) |
| ≤ 80 ms | ≤ 20 ms | stride 미적용 또는 다른 원인 | 코드 레벨 stride 확인 |
| T2+T3 ≥ 50 ms | — | 720p 업스케일 후 전처리 병목 | Phase 3 (해상도 분리) 필수 |

---

## 3. Phase 1 — 병목 측정 도구 구현 (TDD)

### 3.1 테스트 먼저

**파일:** `tests/test_inference_profiler.py`

```python
import pytest
import numpy as np
import time
from edge.inference_profiler import InferenceProfiler, ProfileResult


class TestInferenceProfiler:
    """병목 측정 도구 단위 테스트"""

    def test_profile_result_has_all_fields(self):
        """ProfileResult가 필수 타이밍 필드를 모두 가져야 한다"""
        result = ProfileResult(
            capture_ms=10.0,
            resize_ms=5.0,
            normalize_ms=2.0,
            inference_ms=120.0,
            postprocess_ms=3.0,
        )
        assert result.total_ms == pytest.approx(140.0, rel=0.01)
        assert result.inference_fps == pytest.approx(1000.0 / 120.0, rel=0.01)

    def test_profile_result_bottleneck_identification(self):
        """가장 시간이 긴 단계를 bottleneck으로 반환해야 한다"""
        result = ProfileResult(
            capture_ms=8.0,
            resize_ms=3.0,
            normalize_ms=2.0,
            inference_ms=170.0,  # 가장 큼
            postprocess_ms=2.0,
        )
        assert result.bottleneck == "inference"

    def test_profile_result_bottleneck_capture(self):
        """캡처가 느릴 때 bottleneck이 capture로 식별되어야 한다"""
        result = ProfileResult(
            capture_ms=80.0,   # 가장 큼
            resize_ms=5.0,
            normalize_ms=2.0,
            inference_ms=60.0,
            postprocess_ms=2.0,
        )
        assert result.bottleneck == "capture"

    def test_profiler_runs_n_iterations(self):
        """N회 반복 측정 후 N개의 결과를 반환해야 한다"""
        profiler = InferenceProfiler(model_path=None, use_mock=True)
        results = profiler.run(n_iterations=10, input_shape=(320, 320))
        assert len(results) == 10

    def test_profiler_summary_stats(self):
        """summary가 mean/p50/p95/p99를 포함해야 한다"""
        profiler = InferenceProfiler(model_path=None, use_mock=True)
        results = profiler.run(n_iterations=20, input_shape=(320, 320))
        summary = profiler.summarize(results)

        assert "inference_ms" in summary
        assert all(k in summary["inference_ms"] for k in ["mean", "p50", "p95", "p99"])
        assert "bottleneck" in summary
        assert "estimated_fps" in summary

    def test_profiler_thread_count_comparison(self):
        """스레드 수에 따른 추론 시간 비교 결과를 반환해야 한다"""
        profiler = InferenceProfiler(model_path=None, use_mock=True)
        comparison = profiler.compare_thread_counts(
            thread_counts=[1, 2, 4],
            n_iterations=5,
            input_shape=(320, 320),
        )
        assert set(comparison.keys()) == {1, 2, 4}
        for t in [1, 2, 4]:
            assert "mean_inference_ms" in comparison[t]
            assert "fps" in comparison[t]

    def test_profiler_resolution_comparison(self):
        """해상도별 전처리 + 추론 시간 비교 결과를 반환해야 한다"""
        profiler = InferenceProfiler(model_path=None, use_mock=True)
        comparison = profiler.compare_input_resolutions(
            resolutions=[(160, 160), (320, 320), (640, 640)],
            n_iterations=5,
        )
        assert len(comparison) == 3
        # 해상도가 클수록 추론 시간이 길어야 함
        fps_160 = comparison[(160, 160)]["fps"]
        fps_640 = comparison[(640, 640)]["fps"]
        assert fps_160 > fps_640

    def test_profiler_saves_report_json(self, tmp_path):
        """측정 결과를 JSON 파일로 저장해야 한다"""
        import json
        report_path = tmp_path / "profiler_report.json"
        profiler = InferenceProfiler(model_path=None, use_mock=True)
        results = profiler.run(n_iterations=5, input_shape=(320, 320))
        profiler.save_report(results, str(report_path))

        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert "summary" in data
        assert "raw_results" in data
        assert "bottleneck" in data["summary"]
```

### 3.2 구현

**파일:** `edge/inference_profiler.py`

```python
"""
YOLO 추론 파이프라인 병목 측정 도구.
각 단계(캡처 시뮬레이션, resize, normalize, ONNX 추론, 후처리)를
독립적으로 측정하고 bottleneck을 특정한다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class ProfileResult:
    capture_ms: float
    resize_ms: float
    normalize_ms: float
    inference_ms: float
    postprocess_ms: float

    @property
    def total_ms(self) -> float:
        return (
            self.capture_ms
            + self.resize_ms
            + self.normalize_ms
            + self.inference_ms
            + self.postprocess_ms
        )

    @property
    def inference_fps(self) -> float:
        return 1000.0 / self.inference_ms if self.inference_ms > 0 else 0.0

    @property
    def bottleneck(self) -> str:
        stages = {
            "capture": self.capture_ms,
            "resize": self.resize_ms,
            "normalize": self.normalize_ms,
            "inference": self.inference_ms,
            "postprocess": self.postprocess_ms,
        }
        return max(stages, key=stages.get)


class InferenceProfiler:
    def __init__(self, model_path: Optional[str], use_mock: bool = False):
        self.model_path = model_path
        self.use_mock = use_mock
        self._session = None

        if not use_mock and model_path:
            self._load_session(model_path, num_threads=4)

    def _load_session(self, model_path: str, num_threads: int = 4):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = num_threads
        opts.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        self._session = ort.InferenceSession(model_path, opts)

    def _mock_inference(self, inp: np.ndarray) -> np.ndarray:
        """실제 모델 없이 추론 시간을 시뮬레이션한다."""
        h, w = inp.shape[2], inp.shape[3]
        time.sleep(0.10)  # 100ms mock latency
        return np.random.rand(1, 56, 8400).astype(np.float32)

    def _timed(self, fn, *args) -> Tuple[float, any]:
        t0 = time.perf_counter()
        result = fn(*args)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return elapsed_ms, result

    def run(
        self,
        n_iterations: int,
        input_shape: Tuple[int, int],
        capture_source: Optional[np.ndarray] = None,
    ) -> List[ProfileResult]:
        results = []
        h, w = input_shape

        for _ in range(n_iterations):
            # [T1] 캡처 시뮬레이션
            if capture_source is not None:
                capture_ms, raw_frame = self._timed(lambda: capture_source.copy())
            else:
                capture_ms, raw_frame = self._timed(
                    lambda: np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
                )

            # [T2] resize
            resize_ms, resized = self._timed(cv2.resize, raw_frame, (w, h))

            # [T3] normalize
            def _normalize(img):
                img = img.astype(np.float32) / 255.0
                img = img.transpose(2, 0, 1)[np.newaxis]
                return np.ascontiguousarray(img)

            normalize_ms, inp = self._timed(_normalize, resized)

            # [T4] 추론
            if self.use_mock or self._session is None:
                inference_ms, output = self._timed(self._mock_inference, inp)
            else:
                input_name = self._session.get_inputs()[0].name
                inference_ms, output = self._timed(
                    self._session.run, None, {input_name: inp}
                )

            # [T5] 후처리 시뮬레이션
            postprocess_ms, _ = self._timed(
                lambda o: o[0] if isinstance(o, list) else o, output
            )

            results.append(
                ProfileResult(
                    capture_ms=capture_ms,
                    resize_ms=resize_ms,
                    normalize_ms=normalize_ms,
                    inference_ms=inference_ms,
                    postprocess_ms=postprocess_ms,
                )
            )

        return results

    def summarize(self, results: List[ProfileResult]) -> Dict:
        def stats(values: List[float]) -> Dict:
            arr = np.array(values)
            return {
                "mean": float(np.mean(arr)),
                "p50": float(np.percentile(arr, 50)),
                "p95": float(np.percentile(arr, 95)),
                "p99": float(np.percentile(arr, 99)),
            }

        inference_times = [r.inference_ms for r in results]
        total_times = [r.total_ms for r in results]

        summary = {
            "capture_ms": stats([r.capture_ms for r in results]),
            "resize_ms": stats([r.resize_ms for r in results]),
            "normalize_ms": stats([r.normalize_ms for r in results]),
            "inference_ms": stats(inference_times),
            "postprocess_ms": stats([r.postprocess_ms for r in results]),
            "total_ms": stats(total_times),
            "estimated_fps": float(1000.0 / np.mean(inference_times)),
            "bottleneck": results[len(results) // 2].bottleneck,
        }
        return summary

    def compare_thread_counts(
        self,
        thread_counts: List[int],
        n_iterations: int,
        input_shape: Tuple[int, int],
    ) -> Dict[int, Dict]:
        comparison = {}
        for t in thread_counts:
            if not self.use_mock and self.model_path:
                self._load_session(self.model_path, num_threads=t)
            results = self.run(n_iterations=n_iterations, input_shape=input_shape)
            summary = self.summarize(results)
            comparison[t] = {
                "mean_inference_ms": summary["inference_ms"]["mean"],
                "p95_inference_ms": summary["inference_ms"]["p95"],
                "fps": summary["estimated_fps"],
            }
        return comparison

    def compare_input_resolutions(
        self,
        resolutions: List[Tuple[int, int]],
        n_iterations: int,
    ) -> Dict[Tuple[int, int], Dict]:
        comparison = {}
        for res in resolutions:
            results = self.run(n_iterations=n_iterations, input_shape=res)
            summary = self.summarize(results)
            comparison[res] = {
                "mean_total_ms": summary["total_ms"]["mean"],
                "mean_inference_ms": summary["inference_ms"]["mean"],
                "mean_resize_ms": summary["resize_ms"]["mean"],
                "fps": summary["estimated_fps"],
            }
        return comparison

    def save_report(self, results: List[ProfileResult], path: str) -> None:
        summary = self.summarize(results)
        raw = [
            {
                "capture_ms": r.capture_ms,
                "resize_ms": r.resize_ms,
                "normalize_ms": r.normalize_ms,
                "inference_ms": r.inference_ms,
                "postprocess_ms": r.postprocess_ms,
                "total_ms": r.total_ms,
                "bottleneck": r.bottleneck,
            }
            for r in results
        ]
        report = {"summary": summary, "raw_results": raw}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
```

### 3.3 실행 방법

```bash
# 실기기에서 실제 모델로 측정
python -m tools.run_inference_profiler \
  --model edge/models/yolo26s-pose.onnx \
  --iterations 30 \
  --compare-threads \
  --compare-resolutions \
  --output reports/profiler/$(date +%Y%m%d_%H%M%S)_profile.json

# 측정 결과 예시 출력
# ─────────────────────────────────────
# capture_ms   mean=8.2   p95=12.1
# resize_ms    mean=2.1   p95=3.4
# normalize_ms mean=1.8   p95=2.9
# inference_ms mean=187.4 p95=201.3   ← 여기가 병목이면 Phase 2
# postprocess  mean=1.2   p95=1.8
# ─────────────────────────────────────
# bottleneck: inference
# estimated_fps: 5.33
```

**파일:** `tools/run_inference_profiler.py`

```python
#!/usr/bin/env python3
"""
실기기 YOLO 추론 병목 측정 실행 스크립트.
결과를 JSON으로 저장하고 콘솔에 요약을 출력한다.
"""
import argparse
import json
from pathlib import Path
from edge.inference_profiler import InferenceProfiler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="edge/models/yolo26s-pose.onnx")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--compare-threads", action="store_true")
    parser.add_argument("--compare-resolutions", action="store_true")
    parser.add_argument("--output", default="reports/profiler/profile.json")
    args = parser.parse_args()

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    profiler = InferenceProfiler(model_path=args.model)

    print(f"\n[1] 기본 측정 ({args.iterations}회, 320×320)")
    results = profiler.run(n_iterations=args.iterations, input_shape=(320, 320))
    summary = profiler.summarize(results)

    for stage in ["capture_ms", "resize_ms", "normalize_ms", "inference_ms", "postprocess_ms"]:
        s = summary[stage]
        print(f"  {stage:<18} mean={s['mean']:.1f}ms  p95={s['p95']:.1f}ms")
    print(f"  → bottleneck: {summary['bottleneck']}")
    print(f"  → estimated_fps: {summary['estimated_fps']:.2f}")

    report = {"basic": summary}

    if args.compare_threads:
        print(f"\n[2] 스레드 수 비교 (1/2/4)")
        comparison = profiler.compare_thread_counts(
            thread_counts=[1, 2, 4], n_iterations=10, input_shape=(320, 320)
        )
        for t, v in comparison.items():
            print(f"  threads={t}  fps={v['fps']:.2f}  p95={v['p95_inference_ms']:.1f}ms")
        report["thread_comparison"] = comparison

    if args.compare_resolutions:
        print(f"\n[3] 해상도별 비교 (160/320/640)")
        res_comparison = profiler.compare_input_resolutions(
            resolutions=[(160, 160), (320, 320), (640, 640)], n_iterations=10
        )
        for res, v in res_comparison.items():
            print(f"  {res[0]}×{res[1]}  fps={v['fps']:.2f}  total={v['mean_total_ms']:.1f}ms")
        report["resolution_comparison"] = {str(k): v for k, v in res_comparison.items()}

    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n결과 저장: {args.output}")


if __name__ == "__main__":
    main()
```

---

## 4. Phase 2 — ONNX 추론 최적화 (TDD)

> Phase 1 측정 결과에서 `inference_ms ≥ 150ms`이면 이 단계를 진행한다.

### 4.1 테스트 먼저

**파일:** `tests/test_optimized_pose_estimator.py`

```python
import pytest
import numpy as np
from edge.optimized_pose_estimator import OptimizedPoseEstimator, OnnxSessionConfig


class TestOnnxSessionConfig:
    """ONNX 세션 설정 단위 테스트"""

    def test_default_config_uses_4_threads(self):
        config = OnnxSessionConfig()
        assert config.intra_op_num_threads == 4

    def test_config_validates_thread_range(self):
        with pytest.raises(ValueError, match="1~8"):
            OnnxSessionConfig(intra_op_num_threads=0)
        with pytest.raises(ValueError, match="1~8"):
            OnnxSessionConfig(intra_op_num_threads=9)

    def test_config_graph_optimization_enabled_by_default(self):
        config = OnnxSessionConfig()
        assert config.enable_graph_optimization is True


class TestOptimizedPoseEstimator:
    """최적화된 추론기 단위 테스트 (mock 모드)"""

    @pytest.fixture
    def estimator(self):
        return OptimizedPoseEstimator(model_path=None, use_mock=True)

    def test_infer_returns_keypoints_and_bboxes(self, estimator):
        """추론 결과에 keypoints와 bboxes가 포함되어야 한다"""
        frame = np.random.randint(0, 255, (320, 320, 3), dtype=np.uint8)
        result = estimator.infer(frame)
        assert "keypoints" in result
        assert "bboxes" in result
        assert "inference_ms" in result

    def test_infer_keypoints_shape(self, estimator):
        """keypoints는 (N_persons, 17, 3) 형태여야 한다 (x, y, conf)"""
        frame = np.random.randint(0, 255, (320, 320, 3), dtype=np.uint8)
        result = estimator.infer(frame)
        for kp in result["keypoints"]:
            assert kp.shape[1] == 17
            assert kp.shape[2] == 3

    def test_infer_records_input_size(self, estimator):
        """추론 결과에 실제 입력 해상도가 기록되어야 한다"""
        frame = np.random.randint(0, 255, (320, 320, 3), dtype=np.uint8)
        result = estimator.infer(frame)
        assert result["input_size"] == (320, 320)

    def test_thread_count_affects_session_config(self):
        """스레드 수 설정이 세션에 반영되어야 한다"""
        config = OnnxSessionConfig(intra_op_num_threads=4)
        estimator = OptimizedPoseEstimator(
            model_path=None, use_mock=True, config=config
        )
        assert estimator.config.intra_op_num_threads == 4

    def test_stride_skips_inference_frames(self, estimator):
        """stride=3이면 3프레임 중 1프레임만 추론하고 나머지는 이전 결과를 반환해야 한다"""
        estimator_strided = OptimizedPoseEstimator(
            model_path=None, use_mock=True, inference_stride=3
        )
        frames = [
            np.random.randint(0, 255, (320, 320, 3), dtype=np.uint8)
            for _ in range(9)
        ]
        infer_count = 0
        for frame in frames:
            result = estimator_strided.infer(frame)
            if result.get("actually_inferred"):
                infer_count += 1
        # 9프레임 중 3번만 실제 추론 (stride=3)
        assert infer_count == 3

    def test_inference_ms_is_recorded(self, estimator):
        """inference_ms가 0보다 커야 한다"""
        frame = np.random.randint(0, 255, (320, 320, 3), dtype=np.uint8)
        result = estimator.infer(frame)
        assert result["inference_ms"] > 0
```

### 4.2 구현

**파일:** `edge/optimized_pose_estimator.py`

```python
"""
최적화된 YOLO pose ONNX 추론기.
- intra_op_num_threads=4 (Pi5 코어 최대 활용)
- graph_optimization_level=ORT_ENABLE_ALL
- inference_stride 지원 (stride N이면 N프레임 중 1회만 추론)
- 추론 시간을 결과에 포함
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class OnnxSessionConfig:
    intra_op_num_threads: int = 4
    inter_op_num_threads: int = 1
    enable_graph_optimization: bool = True

    def __post_init__(self):
        if not (1 <= self.intra_op_num_threads <= 8):
            raise ValueError(f"intra_op_num_threads는 1~8이어야 합니다: {self.intra_op_num_threads}")


class OptimizedPoseEstimator:
    def __init__(
        self,
        model_path: Optional[str],
        use_mock: bool = False,
        config: Optional[OnnxSessionConfig] = None,
        inference_stride: int = 1,
    ):
        self.config = config or OnnxSessionConfig()
        self.inference_stride = inference_stride
        self.use_mock = use_mock
        self._session = None
        self._frame_count = 0
        self._last_result: Optional[Dict] = None

        if not use_mock and model_path:
            self._init_session(model_path)

    def _init_session(self, model_path: str):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.config.intra_op_num_threads
        opts.inter_op_num_threads = self.config.inter_op_num_threads
        if self.config.enable_graph_optimization:
            opts.graph_optimization_level = (
                ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
        self._session = ort.InferenceSession(model_path, opts)

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        img = frame.astype(np.float32) / 255.0
        img = img.transpose(2, 0, 1)[np.newaxis]
        return np.ascontiguousarray(img)

    def _mock_output(self, input_size: Tuple[int, int]) -> Dict:
        """테스트용 mock 추론 결과"""
        time.sleep(0.08)  # 80ms 시뮬레이션
        return {
            "keypoints": [np.random.rand(1, 17, 3).astype(np.float32)],
            "bboxes": [np.array([[50, 50, 200, 300, 0.9]])],
            "input_size": input_size,
            "inference_ms": 80.0,
            "actually_inferred": True,
        }

    def _should_infer(self) -> bool:
        return self._frame_count % self.inference_stride == 0

    def infer(self, frame: np.ndarray) -> Dict:
        """
        frame: 이미 추론 입력 크기로 resize된 프레임 (예: 320×320)
        반환: keypoints, bboxes, inference_ms, input_size, actually_inferred
        """
        self._frame_count += 1
        h, w = frame.shape[:2]

        if not self._should_infer() and self._last_result is not None:
            result = dict(self._last_result)
            result["actually_inferred"] = False
            return result

        if self.use_mock or self._session is None:
            result = self._mock_output((w, h))
        else:
            inp = self._preprocess(frame)
            input_name = self._session.get_inputs()[0].name
            t0 = time.perf_counter()
            outputs = self._session.run(None, {input_name: inp})
            inference_ms = (time.perf_counter() - t0) * 1000.0
            keypoints, bboxes = self._parse_output(outputs, w, h)
            result = {
                "keypoints": keypoints,
                "bboxes": bboxes,
                "input_size": (w, h),
                "inference_ms": inference_ms,
                "actually_inferred": True,
            }

        self._last_result = result
        return result

    def _parse_output(
        self, outputs: List[np.ndarray], w: int, h: int
    ) -> Tuple[List[np.ndarray], List[np.ndarray]]:
        """YOLO pose 출력을 keypoints/bboxes로 파싱한다."""
        # YOLO pose 출력: (1, 56, 8400) → NMS → (N, 17, 3)
        raw = outputs[0][0].T  # (8400, 56)
        conf = raw[:, 4]
        mask = conf > 0.25
        raw = raw[mask]

        keypoints, bboxes = [], []
        for det in raw[:5]:  # 최대 5명
            cx, cy, bw, bh = det[:4]
            x1 = (cx - bw / 2) / w
            y1 = (cy - bh / 2) / h
            x2 = (cx + bw / 2) / w
            y2 = (cy + bh / 2) / h
            kp = det[5:].reshape(17, 3).copy()
            kp[:, 0] /= w
            kp[:, 1] /= h
            keypoints.append(kp[np.newaxis])
            bboxes.append(np.array([[x1, y1, x2, y2, det[4]]]))

        return keypoints, bboxes
```

---

## 5. Phase 3 — 해상도 분리 파이프라인 (TDD)

> 캡처는 720p~1080p, YOLO 입력은 320×320으로 완전히 분리한다.

### 5.1 테스트 먼저

**파일:** `tests/test_resolution_pipeline.py`

```python
import pytest
import numpy as np
from edge.resolution_pipeline import ResolutionConfig, ResolutionPipeline


class TestResolutionConfig:
    """해상도 설정 단위 테스트"""

    def test_default_config(self):
        config = ResolutionConfig()
        assert config.capture_width == 1280
        assert config.capture_height == 720
        assert config.inference_width == 320
        assert config.inference_height == 320
        assert config.rtsp_width == 1280
        assert config.rtsp_height == 720

    def test_config_1080p(self):
        config = ResolutionConfig(
            capture_width=1920, capture_height=1080,
            rtsp_width=1920, rtsp_height=1080,
        )
        assert config.capture_width == 1920
        assert config.rtsp_width == 1920

    def test_inference_resolution_independent_of_capture(self):
        """추론 해상도는 캡처 해상도와 독립적으로 설정 가능해야 한다"""
        config = ResolutionConfig(
            capture_width=1920, capture_height=1080,
            inference_width=320, inference_height=320,
        )
        assert config.capture_width == 1920
        assert config.inference_width == 320

    def test_scale_factors(self):
        """scale_x, scale_y가 capture → inference 비율로 계산되어야 한다"""
        config = ResolutionConfig(
            capture_width=1280, capture_height=720,
            inference_width=320, inference_height=320,
        )
        # inference → capture 역변환 비율
        assert config.scale_x == pytest.approx(1280 / 320)   # 4.0
        assert config.scale_y == pytest.approx(720 / 320)    # 2.25


class TestResolutionPipeline:
    """해상도 분리 파이프라인 단위 테스트"""

    @pytest.fixture
    def pipeline_720p(self):
        config = ResolutionConfig(
            capture_width=1280, capture_height=720,
            inference_width=320, inference_height=320,
            rtsp_width=1280, rtsp_height=720,
        )
        return ResolutionPipeline(config)

    @pytest.fixture
    def pipeline_1080p(self):
        config = ResolutionConfig(
            capture_width=1920, capture_height=1080,
            inference_width=320, inference_height=320,
            rtsp_width=1920, rtsp_height=1080,
        )
        return ResolutionPipeline(config)

    def test_get_rtsp_frame_preserves_capture_resolution(self, pipeline_720p):
        """RTSP용 프레임은 캡처 해상도(720p)를 유지해야 한다"""
        raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        rtsp_frame = pipeline_720p.get_rtsp_frame(raw_frame)
        assert rtsp_frame.shape == (720, 1280, 3)

    def test_get_rtsp_frame_1080p(self, pipeline_1080p):
        """1080p 캡처 시 RTSP 프레임도 1080p여야 한다"""
        raw_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        rtsp_frame = pipeline_1080p.get_rtsp_frame(raw_frame)
        assert rtsp_frame.shape == (1080, 1920, 3)

    def test_get_inference_frame_resizes_to_320(self, pipeline_720p):
        """추론용 프레임은 320×320으로 리사이즈되어야 한다"""
        raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        inference_frame = pipeline_720p.get_inference_frame(raw_frame)
        assert inference_frame.shape == (320, 320, 3)

    def test_get_inference_frame_1080p_input(self, pipeline_1080p):
        """1080p 입력도 320×320 추론 프레임으로 변환되어야 한다"""
        raw_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        inference_frame = pipeline_1080p.get_inference_frame(raw_frame)
        assert inference_frame.shape == (320, 320, 3)

    def test_rtsp_frame_is_not_same_object_as_inference_frame(self, pipeline_720p):
        """RTSP 프레임과 추론 프레임은 독립적인 객체여야 한다"""
        raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        rtsp = pipeline_720p.get_rtsp_frame(raw_frame)
        infer = pipeline_720p.get_inference_frame(raw_frame)
        assert rtsp is not infer

    def test_split_returns_both_frames(self, pipeline_720p):
        """split()은 (rtsp_frame, inference_frame) 튜플을 반환해야 한다"""
        raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        rtsp, infer = pipeline_720p.split(raw_frame)
        assert rtsp.shape == (720, 1280, 3)
        assert infer.shape == (320, 320, 3)

    def test_split_does_not_modify_original_frame(self, pipeline_720p):
        """split()이 원본 프레임을 수정하지 않아야 한다"""
        raw_frame = np.ones((720, 1280, 3), dtype=np.uint8) * 128
        original_copy = raw_frame.copy()
        pipeline_720p.split(raw_frame)
        np.testing.assert_array_equal(raw_frame, original_copy)
```

### 5.2 구현

**파일:** `edge/resolution_pipeline.py`

```python
"""
캡처 해상도(720p/1080p)와 YOLO 추론 입력 해상도(320×320)를 분리하는 파이프라인.

- get_rtsp_frame(): 원본 캡처 해상도 그대로 반환 (RTSP/WebRTC용)
- get_inference_frame(): 추론 크기로 resize한 프레임 반환 (YOLO용)
- split(): 두 프레임을 동시에 반환
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import cv2
import numpy as np


@dataclass
class ResolutionConfig:
    capture_width: int = 1280
    capture_height: int = 720
    inference_width: int = 320
    inference_height: int = 320
    rtsp_width: int = 1280
    rtsp_height: int = 720

    @property
    def scale_x(self) -> float:
        """inference → capture (원본) 역변환 x 비율"""
        return self.capture_width / self.inference_width

    @property
    def scale_y(self) -> float:
        """inference → capture (원본) 역변환 y 비율"""
        return self.capture_height / self.inference_height

    @classmethod
    def from_config(cls, cfg: dict) -> "ResolutionConfig":
        return cls(
            capture_width=cfg.get("capture_width", 1280),
            capture_height=cfg.get("capture_height", 720),
            inference_width=cfg.get("inference_width", 320),
            inference_height=cfg.get("inference_height", 320),
            rtsp_width=cfg.get("rtsp_width", 1280),
            rtsp_height=cfg.get("rtsp_height", 720),
        )


class ResolutionPipeline:
    def __init__(self, config: ResolutionConfig):
        self.config = config

    def get_rtsp_frame(self, raw_frame: np.ndarray) -> np.ndarray:
        """
        RTSP/WebRTC 전송용 프레임.
        캡처 해상도 = RTSP 해상도이면 copy만 반환.
        다르면 resize 후 반환.
        """
        h, w = raw_frame.shape[:2]
        target_w, target_h = self.config.rtsp_width, self.config.rtsp_height
        if w == target_w and h == target_h:
            return raw_frame.copy()
        return cv2.resize(raw_frame, (target_w, target_h))

    def get_inference_frame(self, raw_frame: np.ndarray) -> np.ndarray:
        """
        YOLO 추론용 프레임.
        inference_width × inference_height로 resize해서 반환.
        원본 프레임을 수정하지 않는다.
        """
        return cv2.resize(
            raw_frame,
            (self.config.inference_width, self.config.inference_height),
        )

    def split(self, raw_frame: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        원본 프레임을 (rtsp_frame, inference_frame)으로 분리한다.
        두 프레임은 독립적인 객체이며 원본을 수정하지 않는다.
        """
        rtsp_frame = self.get_rtsp_frame(raw_frame)
        inference_frame = self.get_inference_frame(raw_frame)
        return rtsp_frame, inference_frame
```

---

## 6. Phase 4 — 좌표 역변환 및 JSON 스케일링 (TDD)

> YOLO가 320×320 기준으로 뽑은 좌표를 원본(720p/1080p) 기준 픽셀 좌표로 변환한다.
> overlay JSON과 skeleton JSON 모두 이 변환된 좌표를 사용한다.

### 6.1 테스트 먼저

**파일:** `tests/test_coordinate_scaler.py`

```python
import pytest
import numpy as np
from edge.coordinate_scaler import CoordinateScaler, ScaledSkeleton


class TestCoordinateScaler:
    """좌표 역변환 단위 테스트"""

    @pytest.fixture
    def scaler_720p(self):
        # 추론: 320×320, 원본: 1280×720
        return CoordinateScaler(
            inference_w=320, inference_h=320,
            output_w=1280, output_h=720,
        )

    @pytest.fixture
    def scaler_1080p(self):
        # 추론: 320×320, 원본: 1920×1080
        return CoordinateScaler(
            inference_w=320, inference_h=320,
            output_w=1920, output_h=1080,
        )

    # ── 스케일 비율 확인 ─────────────────────────────

    def test_scale_factor_720p(self, scaler_720p):
        assert scaler_720p.scale_x == pytest.approx(4.0)
        assert scaler_720p.scale_y == pytest.approx(2.25)

    def test_scale_factor_1080p(self, scaler_1080p):
        assert scaler_1080p.scale_x == pytest.approx(6.0)
        assert scaler_1080p.scale_y == pytest.approx(3.375)

    # ── bbox 역변환 ──────────────────────────────────

    def test_scale_bbox_pixel_coords(self, scaler_720p):
        """320 기준 bbox 픽셀 좌표 → 720p 픽셀 좌표 역변환"""
        # 320 기준: x1=80, y1=80, x2=240, y2=240
        bbox_320 = np.array([[80.0, 80.0, 240.0, 240.0, 0.9]])
        scaled = scaler_720p.scale_bbox(bbox_320)
        # 720p 기준: x1=320, y1=180, x2=960, y2=540
        assert scaled[0, 0] == pytest.approx(320.0)
        assert scaled[0, 1] == pytest.approx(180.0)
        assert scaled[0, 2] == pytest.approx(960.0)
        assert scaled[0, 3] == pytest.approx(540.0)
        assert scaled[0, 4] == pytest.approx(0.9)  # confidence 유지

    def test_scale_bbox_normalized_coords(self, scaler_720p):
        """0~1 정규화된 bbox → 720p 픽셀 좌표 역변환"""
        # 정규화: x1=0.25, y1=0.25, x2=0.75, y2=0.75
        bbox_norm = np.array([[0.25, 0.25, 0.75, 0.75, 0.9]])
        scaled = scaler_720p.scale_bbox_normalized(bbox_norm)
        assert scaled[0, 0] == pytest.approx(320.0)   # 0.25 * 1280
        assert scaled[0, 1] == pytest.approx(180.0)   # 0.25 * 720
        assert scaled[0, 2] == pytest.approx(960.0)   # 0.75 * 1280
        assert scaled[0, 3] == pytest.approx(540.0)   # 0.75 * 720

    def test_scale_bbox_clamps_to_output_bounds(self, scaler_720p):
        """bbox가 화면 밖으로 나가지 않도록 클램핑되어야 한다"""
        bbox_out = np.array([[-10.0, -10.0, 400.0, 400.0, 0.8]])
        scaled = scaler_720p.scale_bbox(bbox_out)
        assert scaled[0, 0] >= 0.0
        assert scaled[0, 1] >= 0.0
        assert scaled[0, 2] <= 1280.0
        assert scaled[0, 3] <= 720.0

    # ── keypoints 역변환 ─────────────────────────────

    def test_scale_keypoints_pixel_coords(self, scaler_720p):
        """320 기준 keypoint 픽셀 좌표 → 720p 픽셀 좌표 역변환"""
        # 320 기준 코 keypoint: x=160, y=60
        kp_320 = np.array([[[160.0, 60.0, 0.9]] + [[0.0, 0.0, 0.0]] * 16])
        scaled = scaler_720p.scale_keypoints(kp_320)
        # 720p 기준: x=640, y=135
        assert scaled[0, 0, 0] == pytest.approx(640.0)  # 160 * 4.0
        assert scaled[0, 0, 1] == pytest.approx(135.0)  # 60 * 2.25
        assert scaled[0, 0, 2] == pytest.approx(0.9)    # confidence 유지

    def test_scale_keypoints_normalized_coords(self, scaler_720p):
        """0~1 정규화 keypoint → 720p 픽셀 좌표 역변환"""
        # 정규화: x=0.5, y=0.5 (중앙)
        kp_norm = np.array([[[0.5, 0.5, 0.85]] + [[0.0, 0.0, 0.0]] * 16])
        scaled = scaler_720p.scale_keypoints_normalized(kp_norm)
        assert scaled[0, 0, 0] == pytest.approx(640.0)  # 0.5 * 1280
        assert scaled[0, 0, 1] == pytest.approx(360.0)  # 0.5 * 720

    def test_scale_keypoints_confidence_unchanged(self, scaler_720p):
        """keypoint confidence 값은 변환 후에도 유지되어야 한다"""
        kp_320 = np.random.rand(1, 17, 3).astype(np.float32)
        kp_320[:, :, 0] *= 320  # x: 0~320
        kp_320[:, :, 1] *= 320  # y: 0~320
        original_conf = kp_320[:, :, 2].copy()
        scaled = scaler_720p.scale_keypoints(kp_320)
        np.testing.assert_array_almost_equal(scaled[:, :, 2], original_conf)

    def test_scale_keypoints_all_17_joints(self, scaler_720p):
        """17개 keypoint 모두 변환되어야 한다"""
        kp_320 = np.ones((1, 17, 3), dtype=np.float32) * 160.0  # 중앙
        kp_320[:, :, 2] = 0.8  # confidence
        scaled = scaler_720p.scale_keypoints(kp_320)
        assert scaled.shape == (1, 17, 3)
        # 모든 x: 160 * 4.0 = 640
        np.testing.assert_array_almost_equal(scaled[0, :, 0], 640.0)

    # ── ScaledSkeleton JSON 변환 ─────────────────────

    def test_scaled_skeleton_to_json(self, scaler_720p):
        """ScaledSkeleton이 overlay JSON용 dict로 변환되어야 한다"""
        kp_320 = np.random.rand(1, 17, 3).astype(np.float32) * 320
        bbox_320 = np.array([[80.0, 80.0, 240.0, 240.0, 0.9]])

        skeleton = scaler_720p.build_scaled_skeleton(
            track_id="t001",
            keypoints_inference=kp_320,
            bbox_inference=bbox_320,
        )
        d = skeleton.to_dict()
        assert d["track_id"] == "t001"
        assert "bbox" in d
        assert len(d["bbox"]) == 4
        assert "keypoints" in d
        assert len(d["keypoints"]) == 17
        # 각 keypoint: [x, y, conf]
        for kp in d["keypoints"]:
            assert len(kp) == 3

    def test_scaled_skeleton_bbox_in_output_coords(self, scaler_720p):
        """ScaledSkeleton의 bbox는 출력 해상도(720p) 기준이어야 한다"""
        bbox_320 = np.array([[80.0, 80.0, 240.0, 240.0, 0.9]])
        kp_320 = np.zeros((1, 17, 3), dtype=np.float32)

        skeleton = scaler_720p.build_scaled_skeleton("t001", kp_320, bbox_320)
        d = skeleton.to_dict()
        # x1=80*4=320, y1=80*2.25=180, x2=240*4=960, y2=240*2.25=540
        assert d["bbox"][0] == pytest.approx(320.0)
        assert d["bbox"][1] == pytest.approx(180.0)
        assert d["bbox"][2] == pytest.approx(960.0)
        assert d["bbox"][3] == pytest.approx(540.0)

    def test_scaled_skeleton_source_size_recorded(self, scaler_720p):
        """ScaledSkeleton에 원본 해상도(source_width, source_height)가 기록되어야 한다"""
        kp_320 = np.zeros((1, 17, 3), dtype=np.float32)
        bbox_320 = np.array([[0.0, 0.0, 100.0, 100.0, 0.5]])
        skeleton = scaler_720p.build_scaled_skeleton("t001", kp_320, bbox_320)
        d = skeleton.to_dict()
        assert d["source_width"] == 1280
        assert d["source_height"] == 720
```

### 6.2 구현

**파일:** `edge/coordinate_scaler.py`

```python
"""
YOLO 추론 좌표(320×320 기준)를 원본 출력 해상도(720p/1080p 등)로 역변환한다.

사용 흐름:
  1. YOLO 추론 → bbox/keypoints (320 기준 픽셀 or 정규화 좌표)
  2. CoordinateScaler.scale_*() → 출력 해상도 기준 픽셀 좌표
  3. skeleton JSON / overlay JSON에 변환된 좌표 포함
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class ScaledSkeleton:
    """역변환이 완료된 단일 인물의 skeleton 정보"""
    track_id: str
    bbox: List[float]           # [x1, y1, x2, y2] — 출력 해상도 기준 픽셀
    keypoints: List[List[float]]  # [[x, y, conf], ...] 17개 — 출력 해상도 기준 픽셀
    confidence: float
    source_width: int
    source_height: int

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "bbox": self.bbox,
            "keypoints": self.keypoints,
            "confidence": self.confidence,
            "source_width": self.source_width,
            "source_height": self.source_height,
        }


class CoordinateScaler:
    """
    inference 해상도 → output 해상도 좌표 변환기.

    Args:
        inference_w: YOLO 추론 입력 width (예: 320)
        inference_h: YOLO 추론 입력 height (예: 320)
        output_w: 원본 출력 width (예: 1280 for 720p)
        output_h: 원본 출력 height (예: 720 for 720p)
    """

    def __init__(
        self,
        inference_w: int,
        inference_h: int,
        output_w: int,
        output_h: int,
    ):
        self.inference_w = inference_w
        self.inference_h = inference_h
        self.output_w = output_w
        self.output_h = output_h

    @property
    def scale_x(self) -> float:
        return self.output_w / self.inference_w

    @property
    def scale_y(self) -> float:
        return self.output_h / self.inference_h

    def scale_bbox(self, bbox: np.ndarray) -> np.ndarray:
        """
        inference 기준 픽셀 bbox → output 기준 픽셀 bbox.
        bbox shape: (N, 5) — [x1, y1, x2, y2, conf]
        """
        result = bbox.copy().astype(np.float32)
        result[:, 0] = np.clip(bbox[:, 0] * self.scale_x, 0, self.output_w)
        result[:, 1] = np.clip(bbox[:, 1] * self.scale_y, 0, self.output_h)
        result[:, 2] = np.clip(bbox[:, 2] * self.scale_x, 0, self.output_w)
        result[:, 3] = np.clip(bbox[:, 3] * self.scale_y, 0, self.output_h)
        # conf (index 4) 유지
        return result

    def scale_bbox_normalized(self, bbox_norm: np.ndarray) -> np.ndarray:
        """
        0~1 정규화 bbox → output 기준 픽셀 bbox.
        bbox_norm shape: (N, 5) — [x1, y1, x2, y2, conf] (0~1)
        """
        result = bbox_norm.copy().astype(np.float32)
        result[:, 0] = np.clip(bbox_norm[:, 0] * self.output_w, 0, self.output_w)
        result[:, 1] = np.clip(bbox_norm[:, 1] * self.output_h, 0, self.output_h)
        result[:, 2] = np.clip(bbox_norm[:, 2] * self.output_w, 0, self.output_w)
        result[:, 3] = np.clip(bbox_norm[:, 3] * self.output_h, 0, self.output_h)
        return result

    def scale_keypoints(self, keypoints: np.ndarray) -> np.ndarray:
        """
        inference 기준 픽셀 keypoints → output 기준 픽셀 keypoints.
        keypoints shape: (N_persons, 17, 3) — [x, y, conf]
        confidence는 변경하지 않는다.
        """
        result = keypoints.copy().astype(np.float32)
        result[..., 0] = keypoints[..., 0] * self.scale_x   # x
        result[..., 1] = keypoints[..., 1] * self.scale_y   # y
        # conf (index 2) 유지
        return result

    def scale_keypoints_normalized(self, keypoints_norm: np.ndarray) -> np.ndarray:
        """
        0~1 정규화 keypoints → output 기준 픽셀 keypoints.
        keypoints_norm shape: (N_persons, 17, 3) — [x, y, conf] (0~1)
        """
        result = keypoints_norm.copy().astype(np.float32)
        result[..., 0] = keypoints_norm[..., 0] * self.output_w
        result[..., 1] = keypoints_norm[..., 1] * self.output_h
        return result

    def build_scaled_skeleton(
        self,
        track_id: str,
        keypoints_inference: np.ndarray,
        bbox_inference: np.ndarray,
        is_normalized: bool = False,
    ) -> ScaledSkeleton:
        """
        추론 결과(inference 좌표계)를 받아 ScaledSkeleton(output 좌표계)으로 변환한다.

        Args:
            track_id: 트래킹 ID
            keypoints_inference: (1, 17, 3) 또는 (17, 3) — inference 기준
            bbox_inference: (1, 5) 또는 (5,) — inference 기준 [x1,y1,x2,y2,conf]
            is_normalized: True이면 0~1 정규화 좌표, False이면 픽셀 좌표
        """
        # shape 정규화
        kp = keypoints_inference
        if kp.ndim == 2:
            kp = kp[np.newaxis]  # (17, 3) → (1, 17, 3)

        bb = bbox_inference
        if bb.ndim == 1:
            bb = bb[np.newaxis]  # (5,) → (1, 5)

        # 좌표 변환
        if is_normalized:
            scaled_kp = self.scale_keypoints_normalized(kp)
            scaled_bb = self.scale_bbox_normalized(bb)
        else:
            scaled_kp = self.scale_keypoints(kp)
            scaled_bb = self.scale_bbox(bb)

        # dict 변환
        kp_list = scaled_kp[0].tolist()  # [[x, y, conf], ...]
        bbox_list = scaled_bb[0, :4].tolist()  # [x1, y1, x2, y2]
        conf = float(scaled_bb[0, 4])

        return ScaledSkeleton(
            track_id=track_id,
            bbox=bbox_list,
            keypoints=kp_list,
            confidence=conf,
            source_width=self.output_w,
            source_height=self.output_h,
        )
```

---

## 7. Phase 5 — 캡처·추론 스레드 분리 (TDD)

> Phase 1 측정 결과에서 캡처가 추론 루프를 블로킹하고 있으면 이 단계를 진행한다.

### 7.1 테스트 먼저

**파일:** `tests/test_split_pipeline_worker.py`

```python
import pytest
import threading
import time
import numpy as np
from edge.split_pipeline_worker import SplitPipelineWorker, WorkerStats


class TestSplitPipelineWorker:
    """캡처·추론 스레드 분리 단위 테스트"""

    def test_worker_starts_and_stops(self):
        """worker가 정상적으로 시작하고 종료되어야 한다"""
        worker = SplitPipelineWorker(use_mock=True)
        worker.start()
        time.sleep(0.2)
        worker.stop()
        assert not worker.is_running

    def test_capture_thread_runs_at_target_fps(self):
        """캡처 스레드는 target_fps에 근접해야 한다 (±20% 허용)"""
        worker = SplitPipelineWorker(use_mock=True, capture_fps=30)
        worker.start()
        time.sleep(1.0)
        stats = worker.get_stats()
        worker.stop()
        # 30fps ± 20% → 24~36
        assert 24 <= stats.capture_fps <= 36

    def test_inference_runs_independently_of_capture(self):
        """추론 FPS가 캡처 FPS보다 낮아도 캡처는 멈추지 않아야 한다"""
        # mock 추론을 100ms로 설정 → 추론 ~10fps
        worker = SplitPipelineWorker(
            use_mock=True,
            capture_fps=30,
            mock_inference_delay_ms=100,
        )
        worker.start()
        time.sleep(1.5)
        stats = worker.get_stats()
        worker.stop()

        # 캡처는 여전히 ~30fps 근처
        assert stats.capture_fps >= 20
        # 추론은 ~10fps 근처
        assert 5 <= stats.inference_fps <= 15

    def test_frame_queue_drops_old_frames_when_full(self):
        """추론이 느릴 때 queue가 가득 차면 오래된 frame을 drop해야 한다"""
        worker = SplitPipelineWorker(
            use_mock=True,
            capture_fps=30,
            mock_inference_delay_ms=200,  # 5fps 추론
            queue_maxsize=2,
        )
        worker.start()
        time.sleep(1.0)
        stats = worker.get_stats()
        worker.stop()
        # drop이 발생해야 함
        assert stats.dropped_frames > 0

    def test_latest_skeleton_is_accessible(self):
        """최신 skeleton 결과를 get_latest_skeleton()으로 가져올 수 있어야 한다"""
        worker = SplitPipelineWorker(use_mock=True)
        worker.start()
        time.sleep(0.5)
        skeleton = worker.get_latest_skeleton()
        worker.stop()
        # mock 모드에서도 skeleton 결과가 있어야 함
        assert skeleton is not None
        assert "keypoints" in skeleton
        assert "bboxes" in skeleton

    def test_worker_stats_has_required_fields(self):
        """WorkerStats가 필수 필드를 가져야 한다"""
        worker = SplitPipelineWorker(use_mock=True)
        worker.start()
        time.sleep(0.3)
        stats = worker.get_stats()
        worker.stop()
        assert hasattr(stats, "capture_fps")
        assert hasattr(stats, "inference_fps")
        assert hasattr(stats, "dropped_frames")
        assert hasattr(stats, "queue_size")
```

### 7.2 구현

**파일:** `edge/split_pipeline_worker.py`

```python
"""
캡처 스레드와 추론 스레드를 분리한 파이프라인 worker.

- 캡처 스레드: target FPS로 frame을 queue에 넣는다.
- 추론 스레드: queue에서 가장 최신 frame을 꺼내 YOLO 추론을 실행한다.
- queue가 가득 차면 오래된 frame을 drop한다 (캡처 FPS 유지 보장).
- get_latest_skeleton()으로 최신 추론 결과에 비동기 접근 가능.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np


@dataclass
class WorkerStats:
    capture_fps: float = 0.0
    inference_fps: float = 0.0
    dropped_frames: int = 0
    queue_size: int = 0


class SplitPipelineWorker:
    def __init__(
        self,
        use_mock: bool = False,
        capture_fps: int = 30,
        queue_maxsize: int = 2,
        mock_inference_delay_ms: float = 80.0,
        estimator=None,
    ):
        self.use_mock = use_mock
        self.capture_fps = capture_fps
        self.queue_maxsize = queue_maxsize
        self.mock_inference_delay_ms = mock_inference_delay_ms
        self.estimator = estimator

        self._frame_queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self._latest_skeleton: Optional[Dict] = None
        self._skeleton_lock = threading.Lock()
        self._running = False
        self._capture_count = 0
        self._inference_count = 0
        self._dropped = 0
        self._start_time: Optional[float] = None

        self._capture_thread: Optional[threading.Thread] = None
        self._inference_thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        self._running = True
        self._start_time = time.perf_counter()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="CaptureThread"
        )
        self._inference_thread = threading.Thread(
            target=self._inference_loop, daemon=True, name="InferenceThread"
        )
        self._capture_thread.start()
        self._inference_thread.start()

    def stop(self):
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=2.0)
        if self._inference_thread:
            self._inference_thread.join(timeout=2.0)

    def _capture_loop(self):
        interval = 1.0 / self.capture_fps
        while self._running:
            t0 = time.perf_counter()

            # 실기기에서는 camera.capture_array() 호출
            frame = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
            self._capture_count += 1

            # queue가 가득 차면 오래된 frame drop
            if self._frame_queue.full():
                try:
                    self._frame_queue.get_nowait()
                    self._dropped += 1
                except queue.Empty:
                    pass

            try:
                self._frame_queue.put_nowait(frame)
            except queue.Full:
                self._dropped += 1

            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _inference_loop(self):
        while self._running:
            try:
                frame = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self.use_mock or self.estimator is None:
                time.sleep(self.mock_inference_delay_ms / 1000.0)
                result = {
                    "keypoints": [np.random.rand(1, 17, 3).astype(np.float32)],
                    "bboxes": [np.array([[100.0, 100.0, 300.0, 500.0, 0.9]])],
                    "inference_ms": self.mock_inference_delay_ms,
                }
            else:
                result = self.estimator.infer(frame)

            self._inference_count += 1

            with self._skeleton_lock:
                self._latest_skeleton = result

    def get_latest_skeleton(self) -> Optional[Dict]:
        with self._skeleton_lock:
            return dict(self._latest_skeleton) if self._latest_skeleton else None

    def get_stats(self) -> WorkerStats:
        elapsed = time.perf_counter() - self._start_time if self._start_time else 1.0
        return WorkerStats(
            capture_fps=self._capture_count / elapsed,
            inference_fps=self._inference_count / elapsed,
            dropped_frames=self._dropped,
            queue_size=self._frame_queue.qsize(),
        )
```

---

## 8. Phase 6 — 통합 검증

### 8.1 통합 테스트

**파일:** `tests/test_pipeline_integration.py`

```python
import pytest
import numpy as np
import time
from edge.resolution_pipeline import ResolutionConfig, ResolutionPipeline
from edge.coordinate_scaler import CoordinateScaler
from edge.optimized_pose_estimator import OptimizedPoseEstimator, OnnxSessionConfig
from edge.split_pipeline_worker import SplitPipelineWorker


class TestPipelineIntegration:
    """전체 파이프라인 통합 테스트"""

    @pytest.fixture
    def config_720p(self):
        return ResolutionConfig(
            capture_width=1280, capture_height=720,
            inference_width=320, inference_height=320,
            rtsp_width=1280, rtsp_height=720,
        )

    def test_full_pipeline_720p_rtsp_frame_shape(self, config_720p):
        """720p 캡처 → RTSP 프레임이 720p 유지, 추론 프레임이 320×320이어야 한다"""
        pipeline = ResolutionPipeline(config_720p)
        raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        rtsp_frame, inference_frame = pipeline.split(raw_frame)
        assert rtsp_frame.shape == (720, 1280, 3)
        assert inference_frame.shape == (320, 320, 3)

    def test_full_pipeline_yolo_then_scale_back(self, config_720p):
        """320 기준 추론 좌표 → 720p 역변환 후 범위가 720p 내에 있어야 한다"""
        pipeline = ResolutionPipeline(config_720p)
        scaler = CoordinateScaler(320, 320, 1280, 720)
        estimator = OptimizedPoseEstimator(model_path=None, use_mock=True)

        raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        _, inference_frame = pipeline.split(raw_frame)
        result = estimator.infer(inference_frame)

        for bbox in result["bboxes"]:
            scaled = scaler.scale_bbox(bbox)
            assert scaled[0, 0] >= 0 and scaled[0, 2] <= 1280
            assert scaled[0, 1] >= 0 and scaled[0, 3] <= 720

        for kp in result["keypoints"]:
            scaled_kp = scaler.scale_keypoints(kp)
            # 720p 기준 좌표 범위 확인 (mock 결과라 범위가 넓을 수 있음)
            assert scaled_kp.shape == kp.shape

    def test_skeleton_json_contains_source_resolution(self, config_720p):
        """skeleton JSON에 source_width=1280, source_height=720이 포함되어야 한다"""
        scaler = CoordinateScaler(320, 320, 1280, 720)
        kp = np.random.rand(1, 17, 3).astype(np.float32) * 320
        bbox = np.array([[80.0, 80.0, 240.0, 240.0, 0.9]])
        skeleton = scaler.build_scaled_skeleton("t001", kp, bbox)
        d = skeleton.to_dict()
        assert d["source_width"] == 1280
        assert d["source_height"] == 720

    def test_worker_produces_skeleton_with_scaled_coords(self, config_720p):
        """worker가 생성한 skeleton 좌표가 출력 해상도 기준이어야 한다"""
        worker = SplitPipelineWorker(use_mock=True, capture_fps=10)
        worker.start()
        time.sleep(0.5)
        skeleton = worker.get_latest_skeleton()
        worker.stop()
        assert skeleton is not None

    def test_rtsp_frame_not_downscaled_by_inference(self, config_720p):
        """추론 resize가 RTSP 프레임에 영향을 주지 않아야 한다"""
        pipeline = ResolutionPipeline(config_720p)
        raw = np.ones((720, 1280, 3), dtype=np.uint8) * 200
        rtsp, infer = pipeline.split(raw)
        # RTSP는 720p 원본 유지
        assert rtsp.shape[0] == 720 and rtsp.shape[1] == 1280
        # 추론은 320
        assert infer.shape[0] == 320

    def test_1080p_pipeline_end_to_end(self):
        """1080p 전체 파이프라인 통합 테스트"""
        config = ResolutionConfig(
            capture_width=1920, capture_height=1080,
            inference_width=320, inference_height=320,
            rtsp_width=1920, rtsp_height=1080,
        )
        pipeline = ResolutionPipeline(config)
        scaler = CoordinateScaler(320, 320, 1920, 1080)
        estimator = OptimizedPoseEstimator(model_path=None, use_mock=True)

        raw = np.zeros((1080, 1920, 3), dtype=np.uint8)
        rtsp, infer = pipeline.split(raw)
        assert rtsp.shape == (1080, 1920, 3)
        assert infer.shape == (320, 320, 3)

        result = estimator.infer(infer)
        for bbox in result["bboxes"]:
            scaled = scaler.scale_bbox(bbox)
            assert scaled[0, 2] <= 1920
            assert scaled[0, 3] <= 1080
```

### 8.2 config 변경사항

**파일:** `edge/config.raspi_cam01.yaml` 수정

```yaml
camera:
  backend: picamera2
  # 캡처 해상도 — RTSP 품질 기준 (720p 또는 1080p)
  capture_width: 1280
  capture_height: 720
  fps: 30

streaming:
  # RTSP/WebRTC 전송 해상도 (캡처와 동일하게 설정)
  rtsp_width: 1280
  rtsp_height: 720

inference:
  # YOLO 추론 입력 해상도 — 캡처 해상도와 독립적
  inference_width: 320
  inference_height: 320
  stride: 3
  model_path: edge/models/yolo26s-pose.onnx
  onnx_threads: 4              # Pi5 코어 수
  graph_optimization: true

pipeline:
  queue_maxsize: 2             # 추론 스레드 queue 크기
  split_threads: true          # 캡처/추론 스레드 분리 여부
```

---

## 9. 수정 파일 목록 및 완료 기준

### 9.1 파일 목록

| 파일 | 분류 | Phase |
|------|------|-------|
| `edge/inference_profiler.py` | 신규 | Phase 1 |
| `tools/run_inference_profiler.py` | 신규 | Phase 1 |
| `edge/optimized_pose_estimator.py` | 신규 | Phase 2 |
| `edge/resolution_pipeline.py` | 신규 | Phase 3 |
| `edge/coordinate_scaler.py` | 신규 | Phase 4 |
| `edge/split_pipeline_worker.py` | 신규 | Phase 5 |
| `edge/config.raspi_cam01.yaml` | 수정 | Phase 3 |
| `tests/test_inference_profiler.py` | 신규 | Phase 1 |
| `tests/test_optimized_pose_estimator.py` | 신규 | Phase 2 |
| `tests/test_resolution_pipeline.py` | 신규 | Phase 3 |
| `tests/test_coordinate_scaler.py` | 신규 | Phase 4 |
| `tests/test_split_pipeline_worker.py` | 신규 | Phase 5 |
| `tests/test_pipeline_integration.py` | 신규 | Phase 6 |

### 9.2 각 Phase 완료 기준

| Phase | 완료 기준 | 측정 방법 |
|-------|----------|----------|
| Phase 1 | 병목 단계가 특정되고 `profiler_report.json` 생성 | `run_inference_profiler.py` |
| Phase 2 | ONNX threads=4 설정 후 inference FPS ≥ 7 | profiler 재측정 |
| Phase 3 | RTSP 프레임 720p/1080p 유지, 추론 프레임 320×320 분리 | `test_resolution_pipeline.py` |
| Phase 4 | 320→720p 역변환 오차 ≤ 0.5px | `test_coordinate_scaler.py` |
| Phase 5 | 캡처 FPS ≥ 28, 추론 FPS ≥ 8 동시 달성 | `test_split_pipeline_worker.py` |
| Phase 6 | 전체 통합 테스트 통과 + overlay JSON source_width=1280 확인 | `test_pipeline_integration.py` |

### 9.3 최종 목표 수치

| 항목 | 현재 | 목표 |
|------|------|------|
| RTSP 해상도 | 640×360 | **720p (1280×720) or 1080p** |
| YOLO 입력 | 320×320 | 320×320 유지 |
| inference FPS | ~5 | **≥ 10** |
| 캡처 FPS | 30 | 30 유지 |
| skeleton JSON 좌표계 | 320 기준 | **720p/1080p 기준으로 변환** |
| overlay JSON source_width | 640 | **1280 (720p)** |

### 9.4 Phase 실행 순서 (의사결정 트리)

```
Phase 1 실행 → profiler 결과 확인
  │
  ├─ inference_ms ≥ 150ms → Phase 2 (ONNX 최적화) 진행
  │     └→ 개선 후 재측정: ≥ 10 FPS 달성 시 Phase 2 완료
  │
  ├─ capture_ms ≥ 50ms → Phase 5 (스레드 분리) 우선 진행
  │
  ├─ resize_ms + normalize_ms ≥ 30ms → Phase 3 구조가 필수 (720p 캡처 후 resize 분리)
  │
  └─ 어느 경우든 Phase 3 + Phase 4는 RTSP 720p 목표를 위해 반드시 진행
```
