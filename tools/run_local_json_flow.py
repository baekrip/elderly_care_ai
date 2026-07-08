from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import yaml


ROOT = Path(__file__).resolve().parents[1]


def wait_for_health(base_url: str, timeout_sec: int = 30) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/health", timeout=2)
            if response.ok:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local JSON flow test")
    parser.add_argument("--video", default=r"C:\Users\jju03\Downloads\test.mp4")
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--port", type=int, default=8000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    python_exec = ROOT / ".venv_edge_local" / "Scripts" / "python.exe"
    if not python_exec.exists():
        print("missing .venv_edge_local")
        return 1

    server_config = ROOT / "server" / "config.local.yaml"
    server_cmd = [
        str(python_exec),
        "-m",
        "server.main",
        "--config",
        str(server_config),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    temp_config_path = ""
    server_process = subprocess.Popen(server_cmd, cwd=ROOT, env=env)
    try:
        base_url = f"http://127.0.0.1:{args.port}"
        if not wait_for_health(base_url):
            print("server health timeout")
            return 1
        edge_config = yaml.safe_load((ROOT / "edge" / "config.yaml").read_text(encoding="utf-8"))
        edge_config.setdefault("server", {})
        edge_config["server"]["enabled"] = True
        edge_config["server"]["base_url"] = base_url
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False) as temp_file:
            yaml.safe_dump(edge_config, temp_file, allow_unicode=True, sort_keys=False)
            temp_config_path = temp_file.name
        edge_cmd = [
            str(python_exec),
            "-m",
            "edge.main",
            "--config",
            temp_config_path,
            "--source-mode",
            "file",
            "--source",
            args.video,
            "--max-frames",
            str(args.frames),
        ]
        subprocess.check_call(edge_cmd, cwd=ROOT, env=env)
        print("local-json-flow-ok")
        return 0
    finally:
        try:
            os.unlink(temp_config_path)
        except Exception:
            pass
        server_process.terminate()
        try:
            server_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
