from __future__ import annotations

import unittest

from edge.main import validate_runtime_security


class RuntimeSecurityGuardTests(unittest.TestCase):
    def test_skeleton_sender_rejects_public_backend_base_url(self) -> None:
        config = {
            "runtime": {"role": "skeleton_sender"},
            "server": {
                "enabled": True,
                "base_url": "https://api.example.com",
                "skeleton_ws_url": "ws://192.168.45.241:8000/ws/skeleton/raspi_cam01",
            },
            "clip_request_server": {"enabled": True, "host": "192.168.45.29", "port": 8091},
        }

        with self.assertRaisesRegex(ValueError, "external server.base_url"):
            validate_runtime_security(config)

    def test_skeleton_sender_allows_orin_lan_base_url_and_private_bind(self) -> None:
        config = {
            "runtime": {"role": "skeleton_sender"},
            "server": {
                "enabled": True,
                "base_url": "http://192.168.45.241:8000",
                "skeleton_ws_url": "ws://192.168.45.241:8000/ws/skeleton/raspi_cam01",
            },
            "clip_request_server": {"enabled": True, "host": "192.168.45.29", "port": 8091},
        }

        validate_runtime_security(config)

    def test_skeleton_sender_rejects_public_clip_bind_host(self) -> None:
        config = {
            "runtime": {"role": "skeleton_sender"},
            "server": {
                "enabled": True,
                "base_url": "http://192.168.45.241:8000",
                "skeleton_ws_url": "ws://192.168.45.241:8000/ws/skeleton/raspi_cam01",
            },
            "clip_request_server": {"enabled": True, "host": "0.0.0.0", "port": 8091},
        }

        with self.assertRaisesRegex(ValueError, "clip_request_server.host"):
            validate_runtime_security(config)


if __name__ == "__main__":
    unittest.main()
