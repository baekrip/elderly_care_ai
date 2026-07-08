from __future__ import annotations

from device_transfer.Edge.shared.labels import STATIC_POSTURE_LABELS
from tools.static_feature_export_io import JsonObject


def classification_metrics(
    expected: list[int],
    predicted: list[int],
) -> tuple[float, list[list[int]], JsonObject]:
    size = len(STATIC_POSTURE_LABELS)
    matrix = [[0 for _ in range(size)] for _ in range(size)]
    for actual, prediction in zip(expected, predicted, strict=True):
        matrix[actual][prediction] += 1
    total = len(expected)
    accuracy = sum(matrix[index][index] for index in range(size)) / total if total else 0.0
    report: JsonObject = {}
    macro = [0.0, 0.0, 0.0]
    weighted = [0.0, 0.0, 0.0]
    for index, label in enumerate(STATIC_POSTURE_LABELS):
        true_positive = matrix[index][index]
        support = sum(matrix[index])
        predicted_count = sum(row[index] for row in matrix)
        precision = true_positive / predicted_count if predicted_count else 0.0
        recall = true_positive / support if support else 0.0
        f1_score = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        report[label] = {
            "precision": precision,
            "recall": recall,
            "f1-score": f1_score,
            "support": support,
        }
        for metric_index, value in enumerate((precision, recall, f1_score)):
            macro[metric_index] += value
            weighted[metric_index] += value * support
    report["accuracy"] = accuracy
    report["macro avg"] = _average_metrics(macro, size, total)
    report["weighted avg"] = _average_metrics(weighted, total or 1, total)
    return accuracy, matrix, report


def _average_metrics(values: list[float], divisor: int, support: int) -> JsonObject:
    return {
        "precision": values[0] / divisor,
        "recall": values[1] / divisor,
        "f1-score": values[2] / divisor,
        "support": support,
    }
