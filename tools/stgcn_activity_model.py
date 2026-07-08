from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypedDict

import torch
from torch import nn
from torch.utils.data import DataLoader

from device_transfer.Edge.shared.stgcn_model import STGCNBlock, build_coco17_adjacency
from tools.static_feature_export_io import JsonObject


class RunResult(TypedDict):
    best_val_combined_acc: float
    history: list[JsonObject]
    best_state: dict[str, torch.Tensor] | None


@dataclass(frozen=True, slots=True)
class ModelInputError(RuntimeError):
    detail: str

    def __str__(self) -> str:
        return self.detail


class MultiTaskSTGCN(nn.Module):
    """ST-GCN with shared backbone and dual classification heads."""

    def __init__(self, num_activity_classes: int, num_risk_classes: int = 3, in_channels: int = 3) -> None:
        super().__init__()
        adjacency = build_coco17_adjacency()
        self.data_bn = nn.BatchNorm1d(in_channels * 17)
        self.block1 = STGCNBlock(in_channels, 32, adjacency)
        self.block2 = STGCNBlock(32, 64, adjacency, stride=1)
        self.block3 = STGCNBlock(64, 64, adjacency, stride=1)
        self.dropout = nn.Dropout(p=0.2)
        self.activity_head = nn.Linear(64, num_activity_classes)
        self.risk_head = nn.Linear(64, num_risk_classes)
        self._reset_parameters()

    def _reset_parameters(self) -> None:
        for module in self.modules():
            if isinstance(module, (nn.Conv2d, nn.Linear)):
                nn.init.kaiming_uniform_(module.weight, a=math.sqrt(5))
                if module.bias is not None:
                    fan_in, _ = nn.init._calculate_fan_in_and_fan_out(module.weight)
                    bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
                    nn.init.uniform_(module.bias, -bound, bound)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        if x.ndim == 4:
            x = x.permute(0, 3, 1, 2).unsqueeze(-1)
        if x.ndim != 5:
            raise ModelInputError("ST-GCN input must be [N,T,V,C] or [N,C,T,V,M]")
        n, c, t, v, m = x.shape
        x = x.permute(0, 4, 1, 2, 3).contiguous().view(n * m, c, t, v)
        x = x.permute(0, 1, 3, 2).contiguous().view(n * m, c * v, t)
        x = self.data_bn(x)
        x = x.view(n * m, c, v, t).permute(0, 1, 3, 2).contiguous()
        x = self.block3(self.block2(self.block1(x)))
        features = self.dropout(x.mean(dim=-1).mean(dim=-1)).view(n, m, -1).mean(dim=1)
        return self.activity_head(features), self.risk_head(features)


def train_one_run(
    model: MultiTaskSTGCN,
    train_loader: DataLoader,
    val_loader: DataLoader,
    *,
    epochs: int,
    lr: float,
    device: str,
    activity_weight: float = 1.0,
    risk_weight: float = 1.0,
    activity_class_weights: torch.Tensor | None = None,
    risk_class_weights: torch.Tensor | None = None,
) -> RunResult:
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    activity_criterion = nn.CrossEntropyLoss(weight=activity_class_weights.to(device) if activity_class_weights is not None else None)
    risk_criterion = nn.CrossEntropyLoss(weight=risk_class_weights.to(device) if risk_class_weights is not None else None)
    best_val_acc = -1.0
    best_state: dict[str, torch.Tensor] | None = None
    history: list[JsonObject] = []
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        for batch_x, batch_activity_y, batch_risk_y in train_loader:
            batch_x = batch_x.to(device)
            batch_activity_y = batch_activity_y.to(device)
            batch_risk_y = batch_risk_y.to(device)
            activity_logits, risk_logits = model(batch_x)
            loss = activity_weight * activity_criterion(activity_logits, batch_activity_y)
            loss += risk_weight * risk_criterion(risk_logits, batch_risk_y)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        scheduler.step()
        model.eval()
        activity_correct = risk_correct = total = 0
        with torch.no_grad():
            for batch_x, batch_activity_y, batch_risk_y in val_loader:
                activity_logits, risk_logits = model(batch_x.to(device))
                activity_correct += (activity_logits.argmax(1).cpu() == batch_activity_y).sum().item()
                risk_correct += (risk_logits.argmax(1).cpu() == batch_risk_y).sum().item()
                total += batch_activity_y.size(0)
        activity_acc = activity_correct / max(total, 1)
        risk_acc = risk_correct / max(total, 1)
        combined_acc = (activity_acc + risk_acc) / 2
        history.append({"epoch": epoch + 1, "train_loss": round(total_loss / max(len(train_loader), 1), 6), "val_activity_acc": round(activity_acc, 6), "val_risk_acc": round(risk_acc, 6), "val_combined_acc": round(combined_acc, 6)})
        if combined_acc > best_val_acc:
            best_val_acc = combined_acc
            best_state = {key: value.cpu().clone() for key, value in model.state_dict().items()}
    return {"best_val_combined_acc": round(best_val_acc, 6), "history": history, "best_state": best_state}
