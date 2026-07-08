from __future__ import annotations

import unittest

from shared.protocol import PROTOCOL_VERSION, SkeletonFrameBatch, validate_protocol_version


class ProtocolVersionCompatTests(unittest.TestCase):
    def test_skeleton_batch_includes_protocol_version(self) -> None:
        dumped = SkeletonFrameBatch(frames=[]).model_dump(mode="json")

        self.assertEqual(dumped["schema_version"], PROTOCOL_VERSION)

    def test_protocol_version_accepts_same_major_version(self) -> None:
        result = validate_protocol_version({"schema_version": "2.0.0"})

        self.assertTrue(result.compatible)
        self.assertEqual(result.status, "compatible")

    def test_protocol_version_rejects_different_major_version(self) -> None:
        result = validate_protocol_version({"schema_version": "3.0.0"})

        self.assertFalse(result.compatible)
        self.assertEqual(result.status, "major_mismatch")

    def test_missing_protocol_version_is_compatible_with_warning(self) -> None:
        result = validate_protocol_version({})

        self.assertTrue(result.compatible)
        self.assertEqual(result.status, "missing_schema_version")


if __name__ == "__main__":
    unittest.main()
