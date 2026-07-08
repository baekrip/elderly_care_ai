from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass
class PhoneBridgeResult:
    source: str
    description: str


class PhoneBridge:
    def __init__(self, camera_config: dict[str, Any]) -> None:
        self.phone_config = camera_config.get("phone", {})
        self.method = self.phone_config.get("method", "adb_forward")
        self.adb_path = self.phone_config.get("adb_path", "adb")
        self.device_serial = self.phone_config.get("device_serial", "")
        self.local_port = int(self.phone_config.get("local_port", 4747))
        self.remote_port = int(self.phone_config.get("remote_port", self.local_port))
        self.url_template = self.phone_config.get("url_template", "http://127.0.0.1:{port}/video")
        self.manual_source = str(camera_config.get("source", "")).strip()
        self._forward_active = False

    def prepare(self) -> PhoneBridgeResult:
        if self.method == "adb_forward":
            self._run_adb("forward", f"tcp:{self.local_port}", f"tcp:{self.remote_port}")
            self._forward_active = True
            source = self.url_template.format(port=self.local_port)
            return PhoneBridgeResult(source=source, description=f"phone_usb:adb_forward:{source}")

        if self.method == "usb_tethering":
            source = self.manual_source or self.url_template.format(port=self.remote_port)
            return PhoneBridgeResult(source=source, description=f"phone_usb:usb_tethering:{source}")

        if self.method == "manual_url":
            if not self.manual_source:
                raise RuntimeError("phone.manual_url requires camera.source to be set")
            return PhoneBridgeResult(source=self.manual_source, description=f"phone_usb:manual_url:{self.manual_source}")

        raise RuntimeError(f"unsupported phone bridge method: {self.method}")

    def cleanup(self) -> None:
        if self.method == "adb_forward" and self._forward_active:
            self._run_adb("forward", "--remove", f"tcp:{self.local_port}")
            self._forward_active = False

    def _run_adb(self, *args: str) -> None:
        command = [self.adb_path]
        if self.device_serial:
            command.extend(["-s", self.device_serial])
        command.extend(args)
        subprocess.run(command, check=True, capture_output=True, text=True)
