from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app.agent.evaluator import analysis_filter_from_state, build_validated_facts, evaluate, infer_analysis_scope
from app.agent.nodes.planner import build_work_plan, planner
from app.agent.nodes.responder import format_validated_facts
from app.tools.excel_analyzer import inspect_and_analyze


def _xlsx(path: Path, rows: list[dict], sheet: str = "품질") -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, sheet_name=sheet)
    return path


def _rows() -> list[dict]:
    return [
        {"품목": "A200", "공정": "조립", "설비": "2호기", "생산일": "2026-08-03", "생산수량": 100, "불량수량": 2, "불량유형": "조립불량"},
        {"품목": "A200", "공정": "조립", "설비": "2호기", "생산일": "2026-09-03", "생산수량": 100, "불량수량": 10, "불량유형": "조립불량"},
        {"품목": "B100", "공정": "용접", "설비": "2호기", "생산일": "2026-08-03", "생산수량": 80, "불량수량": 1, "불량유형": "기공"},
        {"품목": "B100", "공정": "용접", "설비": "2호기", "생산일": "2026-09-03", "생산수량": 80, "불량수량": 1, "불량유형": "기공"},
        {"품목": "HOUSING", "공정": "가공", "설비": "1호기", "생산일": "2026-08-03", "생산수량": 200, "불량수량": 1, "불량유형": "치수"},
        {"품목": "HOUSING", "공정": "가공", "설비": "1호기", "생산일": "2026-09-03", "생산수량": 200, "불량수량": 1, "불량유형": "치수"},
    ]


def _excel_doc():
    return {
        "id": "doc-1",
        "filename": "quality.xlsx",
        "stored_path": "/uploads/quality.xlsx",
        "raw_text": "[tabular file: quality.xlsx]",
    }


def _state(message, **extra):
    state = {
        "user_message": message,
        "interpreted_goal": extra.pop("interpreted_goal", message),
        "intent": "execute",
        "documents": [_excel_doc()],
        "document": _excel_doc(),
        "pending_clarification": extra.pop("pending_clarification", None),
        "plan": extra.pop("plan", {}),
        "analysis_filter": extra.pop("analysis_filter", {}),
        "conversation_history": extra.pop("conversation_history", []),
        "actions": [],
    }
    state.update(extra)
    return state


class ScopedAnalysisTests(unittest.TestCase):
    def _analyze(self, rows=None, **kwargs):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "quality.xlsx", rows or _rows())
            return inspect_and_analyze(path, filename="quality.xlsx", document_id="doc-1", **kwargs)

    def test_planner_adds_equipment_filter(self):
        plan = build_work_plan(_state("2호기 상태 어때?"))
        self.assertEqual([s.tool for s in plan.steps], ["analyze_excel"])
        self.assertEqual(plan.steps[0].args.get("filter"), {"role": "equipment", "value": "2호기"})
        out = planner(_state("2호기 상태 어때?"))
        self.assertEqual(out["analysis_filter"], {"role": "equipment", "value": "2호기"})

    def test_item_and_process_filters(self):
        item_plan = build_work_plan(_state("A200 품목 어때?"))
        self.assertEqual(item_plan.steps[0].args.get("filter"), {"role": "item", "value": "A200"})
        process_plan = build_work_plan(_state("조립 공정 상태 어때?"))
        self.assertEqual(process_plan.steps[0].args.get("filter"), {"role": "process", "value": "조립"})

    def test_filter_recomputes_subset_metrics(self):
        result = self._analyze(row_filter={"role": "equipment", "value": "2 호기"})
        self.assertTrue(result["ok"])
        ctx = result["filter_context"]
        self.assertTrue(ctx["matched"])
        self.assertEqual(ctx["matched_value"], "2호기")
        scoped = result["within_filter"]["quality_metrics"]
        self.assertEqual(scoped["production_qty_total"], 360)
        self.assertEqual(scoped["defect_qty_total"], 14)
        self.assertAlmostEqual(scoped["defect_rate_pct"], 14 / 360 * 100, places=2)
        self.assertEqual(result["quality_metrics"]["production_qty_total"], 760)
        groups = result["within_filter"]["group_analysis"]
        self.assertTrue(groups.get("by_item"))
        self.assertTrue(groups.get("by_process"))
        self.assertTrue(result["within_filter"]["top_defects"])
        self.assertTrue(result["within_filter"]["period_analysis"]["monthly"])
        item_keys = [row["key"] for row in groups["by_item"]]
        self.assertIn("A200", item_keys)
        self.assertNotIn("HOUSING", item_keys)

    def test_no_match_does_not_pick_similar_equipment(self):
        result = self._analyze(row_filter={"role": "equipment", "value": "9호기"})
        self.assertTrue(result["ok"])
        self.assertFalse(result["filter_context"]["matched"])
        self.assertEqual(result["filter_context"]["error"], "no_match")
        self.assertIsNone(result["within_filter"])

    def test_period_comparison_uses_filtered_subset(self):
        result = self._analyze(row_filter={"role": "equipment", "value": "2호기"})
        facts = build_validated_facts(
            result,
            infer_analysis_scope(_state("2호기 상태 어때?")),
        )
        self.assertEqual(facts["scope"]["value"], "2호기")
        period = facts["period_comparison"]
        self.assertEqual(period["scope"], "filtered")
        scoped_change = result["within_filter"]["change_detection"]
        overall_change = result["change_detection"]
        self.assertEqual(period["defect_rate_delta_pp"], scoped_change["defect_rate_delta_pp"])
        self.assertNotEqual(scoped_change["defect_rate_delta_pp"], overall_change["defect_rate_delta_pp"])
        text = format_validated_facts(facts, user_message="2호기 상태 어때?") or ""
        self.assertNotIn("참고로 전체 데이터 기준으로는", text)
        self.assertIn("2호기", text)

    def test_candidate_factors_from_subset_only(self):
        result = self._analyze(row_filter={"role": "equipment", "value": "2호기"})
        factors = result["candidate_factors"]
        keys = {item.get("key") for item in factors}
        self.assertTrue(factors)
        self.assertNotIn("HOUSING", keys)
        self.assertTrue({"A200", "조립불량", "조립"} & keys)
        for item in factors:
            self.assertTrue(item.get("evidence"))
        facts = build_validated_facts(result, infer_analysis_scope(_state("왜 그런 것 같아?")))
        self.assertTrue(facts.get("candidate_factors"))
        text = format_validated_facts(facts, user_message="왜 그런 것 같아?") or ""
        self.assertIn("우선 확인할 후보", text)
        self.assertNotIn("현재 데이터만으로 원인을 확정하기 어렵습니다", text)

    def test_additional_data_skips_existing_columns(self):
        missing = self._analyze(row_filter={"role": "equipment", "value": "2호기"})
        self.assertIn("설비 온도", missing["additional_data_suggestions"])
        self.assertIn("진동", missing["additional_data_suggestions"])
        rows = [{**row, "온도": 40} for row in _rows()]
        present = self._analyze(rows, row_filter={"role": "equipment", "value": "2호기"})
        joined = " ".join(present["additional_data_suggestions"])
        self.assertNotIn("온도", joined)

    def test_context_carryover_keeps_equipment_filter(self):
        first = planner(_state("2호기 상태 어때?"))
        self.assertEqual(first["analysis_filter"]["value"], "2호기")
        follow = _state(
            "지난주보다 안 좋아졌어?",
            analysis_filter=first["analysis_filter"],
            conversation_history=[{"role": "user", "content": "2호기 상태 어때?"}],
        )
        self.assertEqual(analysis_filter_from_state(follow), {"role": "equipment", "value": "2호기"})
        plan = build_work_plan(follow)
        self.assertEqual(plan.steps[0].args.get("filter"), {"role": "equipment", "value": "2호기"})
        why = _state(
            "왜 그런 것 같아?",
            analysis_filter=first["analysis_filter"],
            conversation_history=[
                {"role": "user", "content": "2호기 상태 어때?"},
                {"role": "user", "content": "지난주보다 안 좋아졌어?"},
            ],
        )
        why_plan = build_work_plan(why)
        self.assertEqual(why_plan.steps[0].args.get("filter"), {"role": "equipment", "value": "2호기"})
        self.assertNotIn("generate_report", [s.tool for s in why_plan.steps])

    def test_overall_item_question_clears_filter(self):
        plan = build_work_plan(
            _state(
                "품목별 불량률 알려줘",
                analysis_filter={"role": "equipment", "value": "2호기"},
            )
        )
        self.assertFalse(plan.steps[0].args.get("filter"))
        self.assertEqual([s.tool for s in plan.steps], ["analyze_excel"])

    def test_filtered_status_does_not_continue_to_report(self):
        excel = self._analyze(row_filter={"role": "equipment", "value": "2호기"})
        plan = build_work_plan(_state("2호기 상태 어때?"))
        result = evaluate(
            {
                **_state("2호기 상태 어때?"),
                "plan": plan.model_dump(),
                "plan_steps": [s.model_dump() for s in plan.steps],
                "tool_calls": [{"tool": "analyze_excel", "args": plan.steps[0].args}],
                "execution_results": [{"tool": "analyze_excel", "ok": True, "detail": "", "data": excel}],
            }
        )
        self.assertEqual(result.status, "pass")
        self.assertNotEqual(result.status, "continue")
        self.assertEqual([s.tool for s in plan.steps], ["analyze_excel"])


if __name__ == "__main__":
    unittest.main()
