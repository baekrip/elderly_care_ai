from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import cv2
import numpy as np
import yaml

from tools.static_feature_export_io import JsonObject, JsonValue

ROI_NAMES: Final[tuple[str, ...]] = ("door", "bed", "floor")
Point = tuple[int, int]
PolygonMap = dict[str, list[Point]]


@dataclass(frozen=True, slots=True)
class RoiContractError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


def _orientation(a: Point, b: Point, c: Point) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    return 0 if value == 0 else (1 if value > 0 else -1)


def _intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    return _orientation(a, b, c) != _orientation(a, b, d) and _orientation(c, d, a) != _orientation(c, d, b)


def validate_polygon(points: list[Point], *, width: int, height: int) -> None:
    if len(set(points)) < 3:
        raise RoiContractError("polygon requires at least three unique points")
    if any(x < 0 or y < 0 or x >= width or y >= height for x, y in points):
        raise RoiContractError("polygon point is outside image bounds")
    segments = list(zip(points, points[1:] + points[:1]))
    for left_index, (a, b) in enumerate(segments):
        for right_index, (c, d) in enumerate(segments):
            if abs(left_index - right_index) <= 1 or {left_index, right_index} == {0, len(segments) - 1}:
                continue
            if _intersects(a, b, c, d):
                raise RoiContractError("polygon has self-intersection")
    area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in segments))
    if area == 0:
        raise RoiContractError("polygon area must be positive")


def merge_roi_config(config: JsonObject, polygons: PolygonMap, *, width: int, height: int) -> JsonObject:
    if set(polygons) != set(ROI_NAMES):
        raise RoiContractError("door, bed, and floor polygons are required")
    merged = dict(config)
    room_rois: JsonObject = {}
    normalized: JsonObject = {}
    for name in ROI_NAMES:
        points = polygons[name]
        validate_polygon(points, width=width, height=height)
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        room_rois[name] = [min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height]
        normalized[name] = [[x / width, y / height] for x, y in points]
    merged["room_rois"] = room_rois
    merged["room_roi_polygons"] = normalized
    return merged


def _read_config(path: Path) -> JsonObject:
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RoiContractError(f"cannot parse config YAML: {path}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise RoiContractError("config YAML root must be a mapping")
    return parsed


def _read_fixture(path: Path) -> PolygonMap:
    try:
        parsed: JsonValue = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoiContractError(f"cannot parse ROI fixture: {path}") from exc
    if not isinstance(parsed, dict) or parsed.get("cancelled") is True:
        raise RoiContractError("ROI selection cancelled")
    polygons: PolygonMap = {}
    for name in ROI_NAMES:
        raw_points = parsed.get(name)
        if not isinstance(raw_points, list):
            raise RoiContractError(f"missing ROI polygon: {name}")
        points: list[Point] = []
        for raw_point in raw_points:
            if not isinstance(raw_point, list) or len(raw_point) != 2 or not all(isinstance(value, int) for value in raw_point):
                raise RoiContractError(f"invalid ROI point: {name}")
            points.append((raw_point[0], raw_point[1]))
        polygons[name] = points
    return polygons


def _select_interactively(image: np.ndarray) -> PolygonMap:
    window = "ROI selector: click points, Enter next ROI, Esc cancel"
    polygons: PolygonMap = {}
    current: list[Point] = []

    def on_mouse(event: int, x: int, y: int, _flags: int, _parameter: JsonValue | None) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            current.append((x, y))

    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)
    try:
        for name in ROI_NAMES:
            current.clear()
            while True:
                canvas = image.copy()
                if current:
                    cv2.polylines(canvas, [np.asarray(current, dtype=np.int32)], False, (0, 255, 255), 2)
                cv2.putText(canvas, f"Select {name}; Enter=accept Esc=cancel", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.imshow(window, canvas)
                key = cv2.waitKey(20) & 0xFF
                if key in (10, 13):
                    validate_polygon(current, width=image.shape[1], height=image.shape[0])
                    polygons[name] = list(current)
                    break
                if key == 27:
                    raise RoiContractError("ROI selection cancelled")
        return polygons
    finally:
        cv2.destroyAllWindows()


def _draw_preview(image: np.ndarray, polygons: PolygonMap) -> np.ndarray:
    canvas = image.copy()
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
    for name, color in zip(ROI_NAMES, colors):
        points = np.asarray(polygons[name], dtype=np.int32)
        cv2.polylines(canvas, [points], True, color, 2)
        cv2.putText(canvas, name, tuple(points[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return canvas


def _atomic_yaml(path: Path, config: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select door/bed/floor ROIs and merge them into YAML")
    parser.add_argument("--image", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-config", required=True)
    parser.add_argument("--fixture-json", help="non-interactive polygon fixture")
    parser.add_argument("--preview-out")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    image = cv2.imread(args.image)
    if image is None:
        print(f"error: cannot read image: {args.image}", file=sys.stderr)
        return 2
    try:
        config = _read_config(Path(args.config))
        polygons = _read_fixture(Path(args.fixture_json)) if args.fixture_json else _select_interactively(image)
        merged = merge_roi_config(config, polygons, width=image.shape[1], height=image.shape[0])
        if args.preview_out:
            preview_path = Path(args.preview_out)
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(preview_path), _draw_preview(image, polygons)):
                raise RoiContractError(f"cannot write preview: {preview_path}")
        _atomic_yaml(Path(args.output_config), merged)
    except (OSError, RoiContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"saved_rois={len(polygons)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
