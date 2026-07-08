from __future__ import annotations

import unittest

from tools.convert_stgcn_tensorrt import _serialized_engine_to_bytes


class _FakeHostMemory:
    def __bytes__(self) -> bytes:
        return b"engine-bytes"


class ConvertStgcnTensorRtTests(unittest.TestCase):
    def test_serialized_engine_to_bytes_accepts_plain_bytes(self) -> None:
        self.assertEqual(_serialized_engine_to_bytes(b"abc"), b"abc")

    def test_serialized_engine_to_bytes_accepts_host_memory_like_object(self) -> None:
        self.assertEqual(_serialized_engine_to_bytes(_FakeHostMemory()), b"engine-bytes")


if __name__ == "__main__":
    unittest.main()
