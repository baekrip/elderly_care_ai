from __future__ import annotations

import unittest

from tools.remaining_plan_preflight import build_doc_state_report, build_offload_decision


class PoseOffloadContractTests(unittest.TestCase):
    def test_missing_numeric_gates_do_not_apply_offload(self) -> None:
        decision = build_offload_decision(
            preflight={"can_run": {"offload_decision": False}},
            benchmark={"status": "blocked", "operational_gate": {"allowed": False, "reasons": ["missing_stage_metrics"]}},
            stability={"long_run_passed": False},
        )

        self.assertEqual(decision["decision"], "do_not_apply_offload_yet")
        self.assertFalse(decision["offload_allowed"])
        self.assertIn("missing_stage_metrics", decision["missing_or_failed_gates"])
        self.assertEqual(
            decision["allowed_future_decisions"],
            ["keep_pi5_local_pose", "try_smaller_pi5_pose_model", "request_new_orin_offload_plan"],
        )

    def test_doc_state_flags_broad_plans_outside_problem_section(self) -> None:
        report = build_doc_state_report(
            config_text="## 1. 명령결과요약\n완료\n\n## 3. 문제점과 해결방안\n- replay clip 부족 해결방안",
            progress_text="진행 완료",
            command_log_text="$omo:start-work",
        )

        self.assertTrue(report["no_remaining_plans_except_errors"])
        self.assertEqual(report["remaining_plan_markers"], [])


if __name__ == "__main__":
    unittest.main()
