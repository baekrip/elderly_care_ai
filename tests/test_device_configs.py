from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeviceConfigTests(unittest.TestCase):
    def test_orin_systemd_templates_use_the_verified_device_account(self) -> None:
        paths = [
            ROOT / "scripts" / "systemd" / "elderly-orin-server.service",
            ROOT / "device_transfer" / "Edge" / "scripts" / "systemd" / "elderly-orin-server.service",
        ]
        for path in paths:
            with self.subTest(path=path):
                service = path.read_text(encoding="utf-8")
                self.assertIn("User=eagleeye", service)
                self.assertIn("Group=eagleeye", service)
                self.assertIn("WorkingDirectory=/home/eagleeye/elderly_care_ai", service)
                self.assertIn("ExecStart=/home/eagleeye/elderly_care_ai/.venv_edge/bin/python", service)
                self.assertNotIn("/home/jetson/", service)

    def test_real_device_configs_exist_and_route_through_orin(self) -> None:
        pi5_config_path = ROOT / "device_transfer" / "camera1" / "edge" / "config.raspi_cam01.yaml"
        orin_edge_config_path = ROOT / "device_transfer" / "Edge" / "edge" / "config.orin_cam02.yaml"
        orin_server_config_path = ROOT / "device_transfer" / "Edge" / "server" / "config.orin.yaml"

        for path in [pi5_config_path, orin_edge_config_path, orin_server_config_path]:
            self.assertTrue(path.exists(), f"missing config: {path}")

        pi5_config = yaml.safe_load(pi5_config_path.read_text(encoding="utf-8"))
        orin_edge_config = yaml.safe_load(orin_edge_config_path.read_text(encoding="utf-8"))
        orin_server_config = yaml.safe_load(orin_server_config_path.read_text(encoding="utf-8"))

        self.assertEqual(pi5_config["camera"]["camera_id"], "raspi_cam01")
        self.assertTrue(pi5_config["server"]["enabled"])
        self.assertEqual(pi5_config["runtime"]["role"], "skeleton_sender")
        self.assertEqual(pi5_config["model"]["backend"], "onnxruntime")
        self.assertEqual(pi5_config["model"]["model_path"], "edge/models/yolo26s-pose-480.onnx")
        self.assertEqual(pi5_config["model"]["imgsz"], 480)
        self.assertEqual(pi5_config["model"]["inference_stride"], 1)
        self.assertEqual(pi5_config["model"]["conf_threshold"], 0.10)
        self.assertEqual(pi5_config["model"]["min_pose_confidence"], 0.10)
        self.assertEqual(pi5_config["model"]["nms_iou_threshold"], 0.65)
        self.assertEqual(pi5_config["model"]["min_bbox_area_ratio"], 0.002)
        self.assertEqual(pi5_config["model"]["max_people"], 1)
        self.assertIn("resolution", pi5_config["camera"])
        self.assertEqual(pi5_config["camera"]["resolution"], [640, 360])
        self.assertEqual(pi5_config["camera"]["processing_resolution"], [640, 360])
        self.assertEqual(pi5_config["camera"]["fps"], 30)
        self.assertTrue(pi5_config["stream"]["overlay_enabled"])
        self.assertEqual(pi5_config["stream"]["overlay_keypoint_threshold"], 0.1)
        self.assertTrue(pi5_config["stream"]["overlay_motion_compensation"])
        self.assertEqual(pi5_config["stream"]["overlay_max_age_ms"], 900)
        self.assertEqual(pi5_config["stream"]["output_url"], "rtsp://127.0.0.1:8554/P001")
        self.assertEqual(
            pi5_config["server"]["skeleton_ws_url"],
            "ws://192.168.45.241:8000/ws/skeleton/raspi_cam01",
        )
        self.assertFalse(pi5_config["tier_classification"]["enabled"])
        self.assertTrue(pi5_config["clip_request_server"]["enabled"])
        self.assertEqual(pi5_config["clip_request_server"]["host"], "192.168.45.29")
        self.assertEqual(pi5_config["clip_request_server"]["port"], 8091)
        self.assertEqual(pi5_config["clip_request_server"]["api_key"], "")
        self.assertEqual(pi5_config["clip_request_server"]["api_key_env"], "EDGE_CLIP_REQUEST_API_KEY")
        self.assertEqual(pi5_config["server"]["ingest_api_key"], "")
        self.assertEqual(pi5_config["server"]["ingest_api_key_env"], "EDGE_INGEST_API_KEY")
        self.assertEqual(pi5_config["server"]["clip_upload_api_key"], "")
        self.assertEqual(pi5_config["server"]["clip_upload_api_key_env"], "CLIP_UPLOAD_API_KEY")
        self.assertEqual(pi5_config["server"]["clip_upload_api_key_header"], "X-API-Key")
        self.assertEqual(pi5_config["privacy"]["face_blur_location"], "orin")
        self.assertTrue(pi5_config["local_output"]["rotation"]["enabled"])
        self.assertEqual(pi5_config["local_output"]["rotation"]["retention_days"], 7)
        self.assertEqual(pi5_config["server"]["base_url"], "http://192.168.45.241:8000")
        self.assertNotIn("13.209.", pi5_config["server"]["base_url"])
        self.assertNotIn("54.180.", pi5_config["server"]["base_url"])

        self.assertEqual(orin_edge_config["camera"]["camera_id"], "orin_cam02")
        self.assertEqual(orin_edge_config["camera"]["fps"], 30)
        self.assertFalse(orin_edge_config["stream"]["overlay_enabled"])
        self.assertEqual(orin_edge_config["stream"]["overlay_max_age_ms"], 500)
        self.assertTrue(orin_edge_config["server"]["enabled"])
        self.assertEqual(orin_edge_config["server"]["base_url"], "http://127.0.0.1:8000")
        self.assertTrue(orin_edge_config["clip_request_server"]["enabled"])
        self.assertEqual(orin_edge_config["clip_request_server"]["host"], "192.168.45.241")
        self.assertEqual(orin_edge_config["clip_request_server"]["port"], 8092)
        self.assertEqual(orin_edge_config["clip_request_server"]["api_key"], "")
        self.assertEqual(orin_edge_config["clip_request_server"]["api_key_env"], "EDGE_CLIP_REQUEST_API_KEY")
        self.assertEqual(orin_edge_config["server"]["ingest_api_key"], "")
        self.assertEqual(orin_edge_config["server"]["ingest_api_key_env"], "EDGE_INGEST_API_KEY")
        self.assertEqual(orin_edge_config["server"]["clip_upload_api_key"], "")
        self.assertEqual(orin_edge_config["server"]["clip_upload_api_key_env"], "CLIP_UPLOAD_API_KEY")
        self.assertEqual(orin_edge_config["server"]["clip_upload_api_key_header"], "X-API-Key")
        self.assertEqual(orin_edge_config["privacy"]["face_blur_location"], "local")
        self.assertTrue(orin_edge_config["local_output"]["rotation"]["enabled"])
        self.assertEqual(orin_edge_config["local_output"]["rotation"]["retention_days"], 7)

        self.assertEqual(orin_server_config["backend"]["base_url"], "")
        self.assertEqual(orin_server_config["backend"]["token"], "")
        self.assertEqual(orin_server_config["backend"]["device_key"], "pi5-home001-cam1")
        self.assertTrue(orin_server_config["backend"]["enabled"])
        self.assertEqual(orin_server_config["backend"]["normal_batch_interval_sec"], 300)
        self.assertEqual(orin_server_config["stgcn"]["model_path"], "server/models/stgcn_fall_binary_fp16.engine")
        self.assertEqual(orin_server_config["stgcn"]["device"], "cuda")
        self.assertEqual(orin_server_config["stgcn"]["backend"], "tensorrt")
        self.assertEqual(orin_server_config["stgcn"]["precision"], "fp16")
        self.assertEqual(orin_server_config["tier_classification"]["model_path"], "edge/models/xgboost_tier_multiclass.json")
        self.assertEqual(
            orin_server_config["tier_classification"]["meta_path"],
            "experiments/behavior_training/reports/xgboost_tier_multiclass_meta.json",
        )
        self.assertNotIn("fall_binary", orin_server_config["tier_classification"]["model_path"])
        self.assertNotIn("fall_binary", orin_server_config["tier_classification"]["meta_path"])
        self.assertEqual(orin_server_config["ingest_auth"]["api_key"], "")
        self.assertEqual(orin_server_config["ingest_auth"]["api_key_env"], "EDGE_INGEST_API_KEY")
        self.assertEqual(orin_server_config["media_server"]["clip_upload_api_key"], "")
        self.assertEqual(orin_server_config["media_server"]["clip_upload_api_key_env"], "CLIP_UPLOAD_API_KEY")
        self.assertEqual(orin_server_config["media_server"]["clip_upload_api_key_header"], "X-API-Key")
        self.assertTrue(orin_server_config["media_server"]["backend_clip_forward_enabled"])
        self.assertTrue(orin_server_config["local_archive"]["rotation"]["enabled"])
        self.assertEqual(orin_server_config["local_archive"]["rotation"]["retention_days"], 7)

    def test_pi5_onnx_input_shape_matches_config_imgsz(self) -> None:
        config_path = ROOT / "device_transfer" / "camera1" / "edge" / "config.raspi_cam01.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model_path = ROOT / "device_transfer" / "camera1" / config["model"]["model_path"]

        import onnxruntime as ort

        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        input_shape = list(session.get_inputs()[0].shape)
        self.assertEqual(input_shape[-2:], [config["model"]["imgsz"], config["model"]["imgsz"]])


if __name__ == "__main__":
    unittest.main()
