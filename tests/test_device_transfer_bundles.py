from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from tools import build_deploy_bundles


ROOT = Path(__file__).resolve().parents[1]


class DeviceTransferBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        build_deploy_bundles.build_device_transfer_bundles()

    def test_camera1_source_bundle_contains_only_pi5_runtime_surface(self) -> None:
        bundle = ROOT / "device_transfer" / "camera1"
        self.assertTrue((bundle / "edge" / "config.raspi_cam01.yaml").exists())
        self.assertTrue((bundle / "shared" / "protocol.py").exists())
        self.assertTrue((bundle / "scripts" / "systemd" / "elderly-edge-cam01.service").exists())
        self.assertTrue((bundle / "scripts" / "systemd" / "elderly-mediamtx.service").exists())

        self.assertFalse((bundle / "docs").exists())
        self.assertFalse((bundle / "server").exists())
        self.assertFalse((bundle / "experiments").exists())

    def test_pi5_user_systemd_units_start_mediamtx_then_edge_at_boot(self) -> None:
        service_dir = ROOT / "device_transfer" / "camera1" / "scripts" / "systemd"
        edge_unit = (service_dir / "elderly-edge-cam01.service").read_text(encoding="utf-8")
        mediamtx_unit = (service_dir / "elderly-mediamtx.service").read_text(encoding="utf-8")

        self.assertNotIn("/home/pi/", edge_unit)
        self.assertIn("WorkingDirectory=/home/eagleeye/elderly_care_ai", edge_unit)
        self.assertIn("Requires=elderly-mediamtx.service", edge_unit)
        self.assertIn("After=elderly-mediamtx.service", edge_unit)
        self.assertIn("Restart=always", edge_unit)
        self.assertIn("WantedBy=default.target", edge_unit)
        self.assertIn("EnvironmentFile=-/home/eagleeye/elderly_care_ai/.env", edge_unit)

        self.assertIn("WorkingDirectory=/home/eagleeye/elderly_care_ai", mediamtx_unit)
        self.assertIn(
            "ExecStart=/home/eagleeye/elderly_care_ai/mediamtx /home/eagleeye/elderly_care_ai/mediamtx.yml",
            mediamtx_unit,
        )
        self.assertIn("Restart=always", mediamtx_unit)
        self.assertIn("WantedBy=default.target", mediamtx_unit)

    def test_root_and_camera1_pi5_systemd_units_match(self) -> None:
        root_service_dir = ROOT / "scripts" / "systemd"
        bundle_service_dir = ROOT / "device_transfer" / "camera1" / "scripts" / "systemd"

        for name in ["elderly-edge-cam01.service", "elderly-mediamtx.service"]:
            with self.subTest(service=name):
                self.assertEqual(
                    (root_service_dir / name).read_text(encoding="utf-8"),
                    (bundle_service_dir / name).read_text(encoding="utf-8"),
                )

    def test_edge_source_bundle_contains_orin_runtime_surface(self) -> None:
        bundle = ROOT / "device_transfer" / "Edge"
        self.assertTrue((bundle / "edge" / "config.orin_cam02.yaml").exists())
        self.assertTrue((bundle / "server" / "config.orin.yaml").exists())
        self.assertTrue((bundle / "server" / "config.yaml").exists())
        self.assertTrue((bundle / "scripts" / "systemd" / "elderly-orin-server.service").exists())
        self.assertTrue((bundle / "scripts" / "systemd" / "elderly-edge-cam02.service").exists())
        self.assertFalse((bundle / "docs").exists())
        for name in [
            "register_training_batch.py",
            "merge_xgboost_feature_batches.py",
            "merge_stgcn_sequence_batches.py",
            "build_yolo_pose_dataset.py",
            "export_xgboost_tier_features.py",
            "export_stgcn_sequences.py",
            "train_xgboost_tier.py",
            "train_stgcn.py",
        ]:
            with self.subTest(tool=name):
                self.assertTrue((bundle / "tools" / name).exists())

    def test_orin_config_has_clip_upload_storage_dir(self) -> None:
        config = yaml.safe_load((ROOT / "device_transfer" / "Edge" / "server" / "config.orin.yaml").read_text(encoding="utf-8"))
        self.assertIn("video_dir", config["storage"])
        self.assertEqual(config["storage"]["video_dir"], "server/storage/videos")

    def test_build_tool_refuses_to_rebuild_device_transfer_source_tree(self) -> None:
        with self.assertRaises(RuntimeError) as context:
            build_deploy_bundles.build_camera1_bundle()
        self.assertIn("device_transfer/camera1 is source-of-truth", str(context.exception))

    def test_secret_file_scan_rejects_env_and_token_files(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "safe.txt").write_text("ok", encoding="utf-8")
            (root / ".env").write_text("APP_TOKEN=secret", encoding="utf-8")
            (root / "nested").mkdir()
            (root / "nested" / "service.token").write_text("secret", encoding="utf-8")

            findings = build_deploy_bundles.find_forbidden_secret_paths(root)

            self.assertEqual(
                sorted(path.name for path in findings),
                [".env", "service.token"],
            )

    def test_root_runtime_folders_are_removed(self) -> None:
        import shutil
        for name in ["edge", "server", "shared"]:
            path = ROOT / name
            if path.exists() and not list(path.rglob("*.py")):
                try:
                    shutil.rmtree(path)
                except OSError as exc:
                    self.fail(f"failed to remove obsolete runtime folder {path}: {exc}")
        self.assertFalse((ROOT / "edge").exists())
        self.assertFalse((ROOT / "server").exists())
        self.assertFalse((ROOT / "shared").exists())

    def test_legacy_deploy_runtime_bundles_are_not_created(self) -> None:
        for name in ["edge_laptop", "edge_jetson", "server_desktop"]:
            with self.subTest(bundle=name):
                self.assertFalse((ROOT / "deploy" / name).exists())

    def test_legacy_deploy_builders_are_disabled(self) -> None:
        for builder in [
            build_deploy_bundles.build_edge_bundle,
            build_deploy_bundles.build_server_bundle,
        ]:
            with self.subTest(builder=builder.__name__):
                with self.assertRaises(RuntimeError) as context:
                    builder()
                self.assertIn("device_transfer is the only device transfer root", str(context.exception))


if __name__ == "__main__":
    unittest.main()
