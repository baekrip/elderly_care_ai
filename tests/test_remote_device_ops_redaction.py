from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.remote_device_ops import CommandResult, _write_explicit_report, redact_sensitive_value


class RemoteDeviceOpsRedactionTests(unittest.TestCase):
    def test_command_result_masks_commands_outputs_hosts_and_presigned_urls(self) -> None:
        result = CommandResult(
            label="probe",
            command=[
                "curl",
                "-H",
                "Authorization: Bearer secret-token",
                "http://192.168.45.241:8000/health?X-Amz-Signature=abcdef",
            ],
            returncode=1,
            stdout="APP_TOKEN=runtime-token http://10.0.0.5/upload?Signature=sig",
            stderr="EDGE_INGEST_API_KEY=edge-secret failed against 172.16.0.10",
        )

        dumped = json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True)

        for forbidden in (
            "secret-token",
            "runtime-token",
            "edge-secret",
            "192.168.45.241",
            "10.0.0.5",
            "172.16.0.10",
            "abcdef",
            "Signature=sig",
        ):
            self.assertNotIn(forbidden, dumped)
        self.assertIn("[REDACTED]", dumped)
        self.assertIn("[REDACTED_HOST]", dumped)

    def test_explicit_report_masks_nested_probe_payloads(self) -> None:
        payload = {
            "command": "curl -H X-Edge-API-Key edge-secret http://192.168.45.29:8091",
            "stdout": {"probe": "APP_TOKEN=token-value", "url": "https://s3.local/file?X-Amz-Credential=cred"},
            "stderr": ["password=123", "host 10.0.0.2"],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            _write_explicit_report(report_path, payload)
            dumped = report_path.read_text(encoding="utf-8")

        for forbidden in (
            "edge-secret",
            "192.168.45.29",
            "token-value",
            "X-Amz-Credential=cred",
            "password=123",
            "10.0.0.2",
        ):
            self.assertNotIn(forbidden, dumped)
        self.assertIn("[REDACTED]", dumped)
        self.assertIn("[REDACTED_HOST]", dumped)

    def test_redact_sensitive_value_preserves_non_sensitive_shape(self) -> None:
        original = {"ok": ["value", 3, {"nested": True}]}
        self.assertEqual(redact_sensitive_value(original), original)


if __name__ == "__main__":
    unittest.main()
