from __future__ import annotations

import argparse
import subprocess
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Short test runner for edge inputs")
    parser.add_argument("mode", choices=["mp4", "url", "usb", "phone"], help="input mode")
    parser.add_argument("source", nargs="?", help="file path, url, or device index")
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--server", action="store_true", help="enable server upload")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    command = [sys.executable, "-m", "edge.main", "--config", args.config]
    if not args.server:
        command.append("--disable-server")
    if args.seconds > 0:
        command.extend(["--run-seconds", str(args.seconds)])
    if args.frames > 0:
        command.extend(["--max-frames", str(args.frames)])

    if args.mode == "mp4":
        command.extend(["--source-mode", "file", "--source", args.source or "sample.mp4"])
    elif args.mode == "url":
        if not args.source:
            raise SystemExit("url mode requires a source URL")
        command.extend(["--source-mode", "url", "--source", args.source])
    elif args.mode == "usb":
        command.extend(["--source-mode", "device", "--source", args.source or "0"])
    elif args.mode == "phone":
        command.extend(["--source-mode", "phone_usb"])
        if args.source:
            command.extend(["--source", args.source])

    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
