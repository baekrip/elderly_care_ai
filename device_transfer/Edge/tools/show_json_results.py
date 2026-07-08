from __future__ import annotations

import argparse
import json
from pathlib import Path


ALIASES = {
    "activity": Path("edge/storage/results/activity_frames.jsonl"),
    "timeline": Path("edge/storage/results/timeline_segments.jsonl"),
    "candidate": Path("edge/storage/results/candidate_windows.jsonl"),
    "server-request": Path("server/storage/results/candidate_requests.jsonl"),
    "stgcn": Path("server/storage/results/stgcn_results.jsonl"),
    "edge-clip-request": Path("clip_json/edge_clip_requests.jsonl"),
    "edge-clip-result": Path("clip_json/edge_clip_results.jsonl"),
    "server-clip-request": Path("server/storage/results/clip_json/server_clip_requests.jsonl"),
    "server-clip-result": Path("server/storage/results/clip_json/server_clip_results.jsonl"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show recent JSONL results")
    parser.add_argument("target", choices=sorted(ALIASES.keys()))
    parser.add_argument("--limit", type=int, default=3)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = ALIASES[args.target]
    if not path.exists():
        print(f"missing: {path}")
        return 1
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[-args.limit:]:
        print(json.dumps(json.loads(line), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
