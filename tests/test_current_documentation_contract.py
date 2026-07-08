from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_doc(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class CurrentDocumentationContractTests(unittest.TestCase):
    def test_config2_has_missing_ticket_details_and_nested_transport_backlog(self) -> None:
        doc = read_doc("docs/구성2.md")
        headings = [
            "EDGE-DEVICE-05",
            "EDGE-AUTO-08",
            "EDGE-FINAL-11",
        ]
        for ticket_id in headings:
            with self.subTest(ticket_id=ticket_id):
                pattern = rf"^### {ticket_id} \[승인완료-실행전확인\]$"
                self.assertEqual(len(re.findall(pattern, doc, flags=re.MULTILINE)), 1)

        device_section = re.search(
            r"^### EDGE-DEVICE-05 \[승인완료-실행전확인\]$(.*?)(?=^### |^## )",
            doc,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(device_section)
        section_text = device_section.group(1) if device_section else ""
        for backlog_item in [
            "reconnect",
            "local queue",
            "sequence_id",
            "dropped frame counter",
        ]:
            with self.subTest(backlog_item=backlog_item):
                self.assertEqual(section_text.count(f"- [ ] {backlog_item}"), 1)

    def test_primary_device_configs_use_known_non_secret_lan_addresses(self) -> None:
        raspi = read_doc("device_transfer/camera1/edge/config.raspi_cam01.yaml")
        orin = read_doc("device_transfer/Edge/edge/config.orin_cam02.yaml")
        self.assertNotIn("PI5_IP", raspi)
        self.assertNotIn("ORIN_IP", raspi)
        self.assertNotIn("ORIN_IP", orin)
        self.assertIn("http://192.168.45.241:8000", raspi)
        self.assertIn("ws://192.168.45.241:8000/ws/skeleton/raspi_cam01", raspi)
        self.assertIn('host: "192.168.45.29"', raspi)
        self.assertIn('host: "192.168.45.241"', orin)
        self.assertIn('output_url: "rtsp://127.0.0.1:8554/P001"', raspi)
        self.assertIn("overlay_show_labels: false", raspi)
        self.assertIn("rtsp://192.168.45.241:8554/orin_cam02", orin)

    def test_endtask_defines_expanded_daily_action_taxonomy(self) -> None:
        doc = read_doc("docs/endtask.md")
        for marker in [
            "standing",
            "walking",
            "sitting",
            "sitting_down",
            "standing_up",
            "lying_rest",
            "no_move_short",
            "near_fall",
            "stumble",
            "fall_confirmed",
        ]:
            with self.subTest(marker=marker):
                self.assertIn(marker, doc)
        self.assertIn("NORMAL 단일 라벨로만 저장하지 않는다", doc)

    def test_progress_has_current_summary_and_open_approval_items(self) -> None:
        doc = read_doc("docs/진행상황.md")
        self.assertIn("최신 요청 반영 정리 (2026-06-04 19:", doc)
        self.assertIn("현재 고정된 실제 주소", doc)
        self.assertIn("승인 전 계획으로 남길 항목", doc)
        self.assertIn("일상행동 세분화는 라벨 재학습 전까지 미적용", doc)

    def test_plan_doc_tracks_streaming_labels_and_optimization_work(self) -> None:
        plan = read_doc("docs/구성.md")
        progress = read_doc("docs/진행상황.md")
        for marker in ["fusion weight", "누운 사람/가려짐/keypoint"]:
            with self.subTest(marker=marker):
                self.assertIn(marker, plan)
        for marker in ["1시간 soak test", "TensorRT", "clip request"]:
            with self.subTest(marker=marker):
                self.assertIn(marker, progress)

    def test_presentation_guide_has_required_test_lines(self) -> None:
        html = read_doc("docs/발표_실사용_가이드.html")
        for marker in [
            "외부 서버 제외 Pi5 + Orin 테스트 라인",
            "로컬 PC 서버 대체 테스트 라인",
            "외부 서버 포함 통합 테스트 라인",
            "MediaMTX 실행 필요",
            "FastAPI Edge Hub 실행 필요",
            "파란 피부 색상 왜곡 해결",
            "https://homecare.p-e.kr",
            "http://54.116.119.98:8889/P001/",
            "Pi5 영상 overlay는 bbox/keypoint/skeleton/status만 표시",
            "행동/위험 판단은 backend event/alert API 결과 또는 JSON 패널에서 확인",
        ]:
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertNotIn("ws://192.168.45.241:8000/ws/overlay/raspi_cam01", html)


if __name__ == "__main__":
    unittest.main()
