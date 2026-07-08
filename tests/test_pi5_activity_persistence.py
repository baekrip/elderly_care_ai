from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from server.db import database as db_module
from server.db.database import create_tables, init_db
from server.db.models import ActivityFrameRecord, RiskEventRecord, TimelineSegmentRecord
from server.services.pi5_activity_persistence import persist_pi5_skeleton_batch
from server.services.pi5_pipeline import Pi5SkeletonPipeline
from shared.protocol import BoundingBox, Keypoint, SkeletonFrame, SkeletonFrameBatch


def _frame(frame_id: str, timestamp_ms: int, speed: float = 0.0) -> SkeletonFrame:
    return SkeletonFrame(
        camera_id="raspi_cam01",
        room_id="living_room",
        frame_id=frame_id,
        timestamp_ms=timestamp_ms,
        capture_ts="2026-05-30T00:00:00Z",
        track_id=1,
        bbox=BoundingBox(x1=0, y1=0, x2=100, y2=200),
        bbox_confidence=0.9,
        keypoints=[Keypoint(x=1.0, y=2.0, confidence=0.8) for _ in range(17)],
        pose_confidence_mean=0.8,
        features={"center_velocity_px_s": speed},
    )


class Pi5ActivityPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        if db_module.engine is not None:
            await db_module.engine.dispose()

    async def test_persist_skeleton_batch_creates_activity_frames_and_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "orin.db"
            init_db(f"sqlite+aiosqlite:///{db_path.as_posix()}")
            await create_tables()
            pipeline = Pi5SkeletonPipeline(
                {
                    "risk_features": {
                        "running_speed_threshold_px_s": 100.0,
                        "danger_speed_threshold_px_s": 250.0,
                    }
                }
            )
            batch = SkeletonFrameBatch(
                frames=[
                    _frame("f-1", 1000, speed=180.0),
                    _frame("f-2", 1100, speed=180.0),
                ]
            )
            pipeline_result = pipeline.handle_batch(batch)

            async with db_module.AsyncSessionLocal() as session:  # type: ignore[misc]
                result = await persist_pi5_skeleton_batch(session, batch, pipeline_result)
                activity_count = await session.scalar(select(func.count(ActivityFrameRecord.id)))
                timeline_count = await session.scalar(select(func.count(TimelineSegmentRecord.id)))

            self.assertEqual(result["activity_frames"], 2)
            self.assertGreaterEqual(result["timeline_segments"], 1)
            self.assertEqual(activity_count, 2)
            self.assertEqual(timeline_count, result["timeline_segments"])
            if db_module.engine is not None:
                await db_module.engine.dispose()

    async def test_duplicate_frame_is_ignored_for_activity_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "orin.db"
            init_db(f"sqlite+aiosqlite:///{db_path.as_posix()}")
            await create_tables()
            batch = SkeletonFrameBatch(frames=[_frame("f-1", 1000)])
            pipeline_result = {"events": []}

            async with db_module.AsyncSessionLocal() as session:  # type: ignore[misc]
                first = await persist_pi5_skeleton_batch(session, batch, pipeline_result)
                second = await persist_pi5_skeleton_batch(session, batch, pipeline_result)
                activity_count = await session.scalar(select(func.count(ActivityFrameRecord.id)))

            self.assertEqual(first["activity_frames"], 1)
            self.assertEqual(second["activity_frames"], 0)
            self.assertEqual(activity_count, 1)
            if db_module.engine is not None:
                await db_module.engine.dispose()

    async def test_pipeline_events_are_persisted_as_risk_events_for_frontend_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "orin.db"
            init_db(f"sqlite+aiosqlite:///{db_path.as_posix()}")
            await create_tables()
            batch = SkeletonFrameBatch(frames=[_frame("f-danger", 1000, speed=300.0)])
            pipeline_result = {
                "events": [
                    {
                        "event_id": "evt-danger-1",
                        "camera_id": "raspi_cam01",
                        "frame_id": "f-danger",
                        "track_id": 1,
                        "event_type": "fall_detected",
                        "state": "SUSPICIOUS",
                        "risk_label": "suspicious",
                        "risk_score": 3,
                        "risk_confidence": 0.55,
                        "raw_score": 0.8,
                        "timestamp_ms": 1000,
                        "capture_ts": "2026-05-30T00:00:00Z",
                        "analysis_ts": "2026-05-30T00:00:01Z",
                    }
                ]
            }

            async with db_module.AsyncSessionLocal() as session:  # type: ignore[misc]
                first = await persist_pi5_skeleton_batch(session, batch, pipeline_result)
                second = await persist_pi5_skeleton_batch(session, batch, pipeline_result)
                risk_count = await session.scalar(select(func.count(RiskEventRecord.id)))
                risk_event = await session.scalar(select(RiskEventRecord))

            self.assertEqual(first["risk_events"], 1)
            self.assertEqual(second["risk_events"], 0)
            self.assertEqual(risk_count, 1)
            self.assertIsNotNone(risk_event)
            assert risk_event is not None
            self.assertEqual(risk_event.event_id, "evt-danger-1")
            self.assertEqual(risk_event.level, "suspicious")
            self.assertEqual(risk_event.label, "fall_detected")
            self.assertEqual(risk_event.source, "pi5_skeleton_ws")
            if db_module.engine is not None:
                await db_module.engine.dispose()


if __name__ == "__main__":
    unittest.main()
