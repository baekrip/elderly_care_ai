from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from server.services.backend_forwarder import BackendResponse, append_pending, mask_sensitive


class TokenMaskingTests(unittest.TestCase):
    def test_mask_sensitive_redacts_nested_auth_tokens_and_upload_urls(self) -> None:
        payload = {
            "headers": {"Authorization": "Bearer secret-app-token"},
            "APP_TOKEN": "secret-app-token",
            "clip": {
                "upload_url": "https://s3.example/upload?X-Amz-Signature=secret",
                "s3_key": "clips/sample.mp4",
            },
            "events": [{"payload": {"aws_secret_access_key": "aws-secret"}}],
        }

        masked = mask_sensitive(payload)
        serialized = json.dumps(masked, ensure_ascii=False)

        self.assertNotIn("secret-app-token", serialized)
        self.assertNotIn("X-Amz-Signature=secret", serialized)
        self.assertNotIn("aws-secret", serialized)
        self.assertEqual(masked["headers"]["Authorization"], "Bearer ***")
        self.assertEqual(masked["clip"]["upload_url"], "***")
        self.assertEqual(masked["clip"]["s3_key"], "clips/sample.mp4")

    def test_append_pending_does_not_write_secrets_to_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_file = Path(tmpdir) / "backend_pending.jsonl"

            append_pending(
                pending_file,
                kind="events_batch",
                url="https://backend.example/api/v1/events/batch?token=secret-query",
                payload={
                    "headers": {"Authorization": "Bearer secret-app-token"},
                    "upload_url": "https://s3.example/upload?signature=secret",
                    "events": [{"payload": {"APP_TOKEN": "secret-app-token"}}],
                },
                response=BackendResponse(ok=False, status_code=500, text="secret-app-token failed"),
            )

            text = pending_file.read_text(encoding="utf-8")

            self.assertNotIn("secret-app-token", text)
            self.assertNotIn("secret-query", text)
            self.assertNotIn("signature=secret", text)
            self.assertIn("Bearer ***", text)


if __name__ == "__main__":
    unittest.main()
