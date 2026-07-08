from __future__ import annotations

import json
from pathlib import Path

def visible_joint_ratio(record: dict) -> float:
    keypoints = record.get("keypoints") or []
    if not keypoints:
        return 0.0
    visible = 0
    for item in keypoints[:17]:
        if isinstance(item, dict):
            confidence = item.get("confidence", item.get("conf", 0.0))
        else:
            confidence = item[2] if len(item) > 2 else 0.0
        if float(confidence) > 0:
            visible += 1
    return visible / 17.0

def main() -> None:
    input_path = Path("experiments/behavior_training/labels/yolo_pose_pseudo_labels.jsonl")
    output_path = Path("experiments/behavior_training/labels/yolo_pose_pseudo_labels_filtered.jsonl")
    
    if not input_path.exists():
        print("Input file not found.")
        return
        
    filtered_rows = []
    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            pose_conf = float(record.get("pose_confidence_mean", 0.0))
            ratio = visible_joint_ratio(record)
            
            # 높은 정확도 임계값 필터 (confidence >= 0.75, ratio >= 0.8)
            if pose_conf >= 0.75 and ratio >= 0.8:
                filtered_rows.append(record)
                
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for row in filtered_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            
    print(f"Filtered pseudo labels: total={len(filtered_rows)} written to {output_path}")

if __name__ == "__main__":
    main()
