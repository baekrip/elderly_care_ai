from __future__ import annotations

import unittest

import torch

from shared.stgcn_model import MiniSTGCN, build_coco17_adjacency


class STGCNModelTests(unittest.TestCase):
    def test_adjacency_shape(self) -> None:
        adjacency = build_coco17_adjacency()
        self.assertEqual(tuple(adjacency.shape), (17, 17))

    def test_forward_shape(self) -> None:
        model = MiniSTGCN(num_classes=3)
        tensor = torch.zeros((2, 3, 24, 17, 1), dtype=torch.float32)
        output = model(tensor)
        self.assertEqual(tuple(output.shape), (2, 3))


if __name__ == "__main__":
    unittest.main()
