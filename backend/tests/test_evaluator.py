from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app.agent.evaluator import MAX_RETRY, evaluate, maybe_llm_evaluate
from app.agent.graph import build_graph
from app.agent.nodes.responder import build_reply_messages, fallback_reply, format_validated_facts
from app.agent.nodes.tool_selector import tool_selector
from app.agent.nodes.verifier import verifier
from app.agent.router import after_verify, route_from_state
from app.agent.work_intent import classify_rule_intent
from app.tools.excel_analyzer import inspect_and_analyze


def _xlsx(path: Path, rows: list[dict], sheet: str = "품질") -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, sheet_name=sheet)
    return path


def _mapping(**cols):
    roles = (
        "item",
        "lot",
        "defect_qty",
        "production_qty",
        "result",
        "defect_type",
        "process",
        "equipment",
        "date",
    )
    return {role: {"column": cols.get(role), "confidence": 1.0 if cols.get(role) else 0.0, "candidates": []} for role in roles}


def _excel_row(data: dict) -> dict:
    return {"tool": "analyze_excel", "ok": data.get("ok", True), "detail": data.get("detail") or "", "data": data}


def _state(message, excel=None, **extra):
    results = [_excel_row(excel)] if excel is not None else extra.pop("execution_results", [])
    state = {
        "intent": "execute",
        "user_message": message,
        "interpreted_goal": extra.pop("interpreted_goal", message),
        "plan": extra.pop("plan", {"steps": [{"tool": "analyze_excel", "args": {"document_id": "doc-1"}}]}),
        "plan_steps": extra.pop("plan_steps", [{"tool": "analyze_excel", "args": {"document_id": "doc-1"}}]),
        "tool_calls": extra.pop("tool_calls", [{"tool": "analyze_excel", "args": {"document_id": "doc-1"}}]),
        "execution_results": results,
        "retry_count": 0,
        "evaluation_count": 0,
        "actions": [],
        "tasks": [],
    }
    state.update(extra)
    return state


def _item_rate_rows():
    return [
        {"품목": "BRACKET", "생산수량": 100, "불량수량": 4, "불량유형": "기공", "생산일": "2026-08-03", "설비": "1호기"},
        {"품목": "HOUSING", "생산수량": 80, "불량수량": 2, "불량유형": "치수", "생산일": "2026-09-03", "설비": "2호기"},
        {"품목": "HOUSING", "생산수량": 80, "불량수량": 6, "불량유형": "치수", "생산일": "2026-09-10", "설비": "2호기"},
    ]


class EvaluatorExcelTests(unittest.TestCase):
    def _analyze(self, rows):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "quality.xlsx", rows)
            return inspect_and_analyze(path, filename="quality.xlsx")

    def test_item_defect_rate_pass(self):
        data = self._analyze(_item_rate_rows())
        result = evaluate(_state("품목별 불량률 알려줘", data))
        self.assertEqual(result.status, "pass")
        self.assertTrue(result.goal_satisfied)
        self.assertIn("defect_rate", result.validated_facts)
        self.assertIsNotNone(result.validated_facts["defect_rate"])
        names = {c.name: c.passed for c in result.checks}
        self.assertTrue(names.get("item_group"))
        self.assertTrue(names.get("defect_rate"))

    def test_item_defect_rate_without_production_clarifies(self):
        rows = [{"품목": "BRACKET", "불량수량": 4}, {"품목": "HOUSING", "불량수량": 2}]
        data = self._analyze(rows)
        result = evaluate(_state("품목별 불량률 알려줘", data))
        self.assertIn(result.status, {"clarify", "fail"})
        self.assertNotIn("defect_rate", result.validated_facts)
        self.assertIn("production_qty", result.missing_requirements)
        reply = fallback_reply(
            {
                **_state("품목별 불량률 알려줘", data),
                "evaluation_status": result.status,
                "evaluation_user_message": result.user_message,
                "validated_facts": result.validated_facts,
            }
        )
        self.assertNotRegex(reply, r"불량률은\s+\d")
        self.assertIn("생산수량", reply)

    def test_source_rate_column_is_not_enough_for_pass(self):
        rows = [{"품목": "BRACKET", "불량률": 2.5}, {"품목": "HOUSING", "불량률": 1.2}]
        data = self._analyze(rows)
        result = evaluate(_state("품목별 불량률 알려줘", data))
        self.assertIn(result.status, {"clarify", "fail"})
        self.assertNotIn("defect_rate", result.validated_facts)
        self.assertTrue(
            "production_qty" in result.missing_requirements or "defect_rate" in result.missing_requirements
        )

    def test_period_comparison_pass(self):
        data = self._analyze(_item_rate_rows())
        result = evaluate(_state("지난주보다 안 좋아졌어?", data))
        self.assertEqual(result.status, "pass")
        names = {c.name: c.passed for c in result.checks}
        self.assertTrue(names.get("date_column"))
        self.assertTrue(names.get("period_comparison"))
        self.assertIn("period_comparison", result.validated_facts)

    def test_period_comparison_without_date_clarifies(self):
        rows = [{"품목": "BRACKET", "생산수량": 100, "불량수량": 4}]
        data = self._analyze(rows)
        result = evaluate(_state("지난주보다 안 좋아졌어?", data))
        self.assertEqual(result.status, "clarify")
        self.assertIn("date_column", result.missing_requirements)
        self.assertIn("날짜", result.user_message or "")

    def test_equipment_status_pass(self):
        data = self._analyze(_item_rate_rows())
        result = evaluate(_state("2호기 상태 어때?", data, interpreted_goal="2호기 최근 상태 분석"))
        self.assertEqual(result.status, "pass")
        names = {c.name: c.passed for c in result.checks}
        self.assertTrue(names.get("equipment_column"))
        self.assertTrue(names.get("equipment_filter"))
        self.assertEqual(result.validated_facts.get("equipment", {}).get("name"), "2호기")

    def test_equipment_without_column_clarifies(self):
        rows = [{"품목": "BRACKET", "생산수량": 100, "불량수량": 4}]
        data = self._analyze(rows)
        result = evaluate(_state("2호기 상태 어때?", data))
        self.assertEqual(result.status, "clarify")
        self.assertIn("equipment_column", result.missing_requirements)
        self.assertIn("설비", result.user_message or "")
        self.assertNotIn("retry", result.status)


class EvaluatorRetryTests(unittest.TestCase):
    def test_tool_failure_can_retry(self):
        result = evaluate(
            _state(
                "품목별 불량률 알려줘",
                {"ok": False, "error": "read_error", "detail": "일시적 읽기 오류"},
            )
        )
        self.assertEqual(result.status, "retry")
        self.assertEqual(result.retry_target, "tools")
        out = verifier(_state("품목별 불량률 알려줘", {"ok": False, "error": "read_error", "detail": "일시적 읽기 오류"}))
        self.assertEqual(out["evaluation_status"], "retry")
        self.assertEqual(after_verify(out), "retry_tools")

    def test_partial_then_retry_then_pass(self):
        first = {
            "ok": True,
            "needs_clarification": False,
            "column_mapping": _mapping(equipment="설비", production_qty="생산수량", defect_qty="불량수량"),
            "quality_metrics": {"production_qty_total": 100, "defect_qty_total": 4, "defect_rate_pct": 4.0},
            "group_analysis": {"by_equipment": [{"key": "1호기", "rows": 10, "defect_qty": 4, "defect_rate_pct": 4.0}]},
        }
        first_eval = evaluate(_state("2호기 상태 어때?", first))
        self.assertEqual(first_eval.status, "retry")
        self.assertEqual(first_eval.retry_args.get("top_n"), 50)
        verified = verifier(_state("2호기 상태 어때?", first))
        self.assertEqual(verified["evaluation_status"], "retry")
        self.assertEqual(after_verify(verified), "retry_tools")
        selected = tool_selector({**_state("2호기 상태 어때?", first), **verified, "intent": "execute"})
        self.assertEqual(selected["tool_calls"][0]["args"].get("top_n"), 50)

        second = dict(first)
        second["group_analysis"] = {
            "by_equipment": [
                {"key": "1호기", "rows": 10, "defect_qty": 4, "defect_rate_pct": 4.0},
                {"key": "2호기", "rows": 6, "defect_qty": 1, "defect_rate_pct": 1.5},
            ]
        }
        second_eval = evaluate(_state("2호기 상태 어때?", second, retry_count=1, tool_calls=selected["tool_calls"]))
        self.assertEqual(second_eval.status, "pass")
        self.assertEqual(second_eval.validated_facts["equipment"]["name"], "2호기")

    def test_max_retry_stops(self):
        data = {
            "ok": True,
            "needs_clarification": False,
            "column_mapping": _mapping(equipment="설비"),
            "group_analysis": {"by_equipment": [{"key": "1호기", "rows": 3}]},
        }
        state = _state("2호기 상태 어때?", data, retry_count=MAX_RETRY, tool_calls=[{"tool": "analyze_excel", "args": {"top_n": 50}}])
        result = evaluate(state)
        self.assertEqual(result.status, "fail")
        self.assertIn("충분히 완료하기 어렵", result.user_message or "")
        out = verifier(state)
        self.assertEqual(out["evaluation_status"], "fail")
        self.assertEqual(after_verify(out), "ok")


class EvaluatorSafetyTests(unittest.TestCase):
    def test_no_cause_hallucination(self):
        facts = {
            "total_production": 10000,
            "total_defects": 210,
            "defect_rate": 2.1,
            "top_defect": {"name": "조립불량", "count": 92},
        }
        text = format_validated_facts(facts, user_message="왜 불량이 늘었어?") or ""
        self.assertIn("원인을 더 좁힐", text)
        for banned in ("작업자", "노후화", "부주의", "공정 이상"):
            self.assertNotIn(banned, text)
        reply = fallback_reply(
            {
                "intent": "execute",
                "evaluation_status": "pass",
                "validated_facts": facts,
                "user_message": "왜 불량이 늘었어?",
                "execution_results": [],
                "actions": [],
            }
        )
        self.assertIn("원인을 더 좁힐", reply)
        self.assertNotIn("현재 데이터만으로 원인을 확정하기 어렵습니다", reply)
        messages = build_reply_messages(
            {
                "user_message": "왜 불량이 늘었어?",
                "validated_facts": facts,
                "evaluation_status": "pass",
                "execution_results": [],
            }
        )
        self.assertIn("원인을 사실처럼 쓰지 마라", messages[0]["content"])
        self.assertIn("validated_facts", messages[1]["content"])

    def test_query_skips_evaluator_loop(self):
        result = evaluate({"intent": "query", "user_message": "안녕", "execution_results": []})
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.checks, [])
        self.assertEqual(route_from_state({"intent": "query"}), "query")
        self.assertEqual(classify_rule_intent({"user_message": "안녕", "documents": []})["verdict"], "CONFIDENT_QUERY")
        compiled = build_graph()
        self.assertIn("responder", compiled.nodes)
        self.assertIn("verifier", compiled.nodes)

    def test_existing_tools_compatible(self):
        ingest_pass = evaluate(
            {
                "intent": "ingest",
                "actions": [{"title": "보고서 작성", "deadline": "2026-10-10"}],
                "tasks": [{"title": "초안"}],
                "execution_results": [{"tool": "create_calendar_events", "ok": True}],
            }
        )
        self.assertEqual(ingest_pass.status, "pass")
        ingest_retry = evaluate(
            {
                "intent": "ingest",
                "actions": [{"title": "보고서 작성", "deadline": "2026-10-10"}],
                "tasks": [{"title": "초안"}],
                "execution_results": [{"tool": "create_tasks", "ok": True}],
                "retry_count": 0,
            }
        )
        self.assertEqual(ingest_retry.status, "retry")
        draft = evaluate(
            {
                "intent": "execute",
                "user_message": "완료보고서 초안 만들어줘",
                "execution_results": [{"tool": "generate_draft", "ok": True, "detail": "초안 저장"}],
                "tool_calls": [{"tool": "generate_draft", "args": {}}],
            }
        )
        self.assertEqual(draft.status, "pass")
        tasks = evaluate(
            {
                "intent": "execute",
                "user_message": "일정 등록해줘",
                "execution_results": [{"tool": "create_tasks", "ok": True, "detail": "Task 1건 준비"}],
                "tool_calls": [{"tool": "create_tasks", "args": {}}],
            }
        )
        self.assertEqual(tasks.status, "pass")
        calendar = evaluate(
            {
                "intent": "execute",
                "user_message": "캘린더에 넣어줘",
                "execution_results": [{"tool": "create_calendar_events", "ok": True, "detail": "Mock Calendar 1건 생성"}],
                "tool_calls": [{"tool": "create_calendar_events", "args": {}}],
            }
        )
        self.assertEqual(calendar.status, "pass")

    def test_llm_hook_is_optional_noop(self):
        data = {
            "ok": True,
            "needs_clarification": False,
            "column_mapping": _mapping(item="품목", production_qty="생산수량", defect_qty="불량수량"),
            "quality_metrics": {"production_qty_total": 100, "defect_qty_total": 2, "defect_rate_pct": 2.0},
            "group_analysis": {"by_item": [{"key": "BRACKET", "defect_qty": 2, "defect_rate_pct": 2.0, "production_qty": 100}]},
        }
        rule = evaluate(_state("품목별 불량률 알려줘", data))
        self.assertEqual(maybe_llm_evaluate(_state("품목별 불량률 알려줘", data), rule).status, rule.status)


if __name__ == "__main__":
    unittest.main()
