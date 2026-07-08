from __future__ import annotations

import math

import torch
from torch import nn


def build_coco17_adjacency() -> torch.Tensor:
    edges = [
        (0, 1), (0, 2), (1, 3), (2, 4),
        (5, 6),
        (5, 7), (7, 9),
        (6, 8), (8, 10),
        (5, 11), (6, 12),
        (11, 12),
        (11, 13), (13, 15),
        (12, 14), (14, 16),
    ]
    joints = 17
    adjacency = torch.eye(joints, dtype=torch.float32)
    for src, dst in edges:
        adjacency[src, dst] = 1.0
        adjacency[dst, src] = 1.0
    degree = adjacency.sum(dim=1)
    degree_inv_sqrt = torch.diag(torch.pow(degree.clamp(min=1.0), -0.5))
    normalized = degree_inv_sqrt @ adjacency @ degree_inv_sqrt
    return normalized


class GraphConv(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, adjacency: torch.Tensor) -> None:
        super().__init__()
        self.register_buffer("adjacency", adjacency)
        self.proj = nn.Conv2d(in_channels, out_channels, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (N, C, T, V)
        x = torch.einsum("nctv,vw->nctw", x, self.adjacency)
        return self.proj(x)


class STGCNBlock(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, adjacency: torch.Tensor, stride: int = 1) -> None:
        super().__init__()
        self.graph = GraphConv(in_channels, out_channels, adjacency)
        self.temporal = nn.Sequential(
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(
                out_channels,
                out_channels,
                kernel_size=(9, 1),
                padding=(4, 0),
                stride=(stride, 1),
            ),
            nn.BatchNorm2d(out_channels),
        )
        if in_channels == out_channels and stride == 1:
            self.residual = nn.Identity()
        else:
            self.residual = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=(stride, 1)),
                nn.BatchNorm2d(out_channels),
            )
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.residual(x)
        x = self.graph(x)
        x = self.temporal(x)
        return self.relu(x + residual)


class MiniSTGCN(nn.Module):
    def __init__(self, num_classes: int, in_channels: int = 3) -> None:
        super().__init__()
        adjacency = build_coco17_adjacency()
        self.data_bn = nn.BatchNorm1d(in_channels * 17)
        self.block1 = STGCNBlock(in_channels, 32, adjacency)
        self.block2 = STGCNBlock(32, 64, adjacency, stride=1)
        self.block3 = STGCNBlock(64, 64, adjacency, stride=1)
        self.dropout = nn.Dropout(p=0.2)
        self.fc = nn.Linear(64, num_classes)
        self._reset_parameters()

    def _reset_parameters(self) -> None:
        for module in self.modules():
            if isinstance(module, (nn.Conv2d, nn.Linear)):
                nn.init.kaiming_uniform_(module.weight, a=math.sqrt(5))
                if module.bias is not None:
                    fan_in, _ = nn.init._calculate_fan_in_and_fan_out(module.weight)
                    bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
                    nn.init.uniform_(module.bias, -bound, bound)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (N, C, T, V, M)
        n, c, t, v, m = x.shape
        x = x.permute(0, 4, 1, 2, 3).contiguous().view(n * m, c, t, v)
        x = x.permute(0, 1, 3, 2).contiguous().view(n * m, c * v, t)
        x = self.data_bn(x)
        x = x.view(n * m, c, v, t).permute(0, 1, 3, 2).contiguous()
        x = self.block1(x)
        x = self.block2(x)
        x = self.block3(x)
        x = x.mean(dim=-1).mean(dim=-1)
        x = self.dropout(x)
        x = x.view(n, m, -1).mean(dim=1)
        return self.fc(x)
