from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent.nodes.planner import build_work_plan
from app.agent.nodes.tool_selector import tool_selector
from app.agent.router import after_plan
from app.agent.semantic_intent import resolve_hybrid_intent
from app.agent.work_intent import classify_rule_intent
from app.schemas.agent import SemanticIntent


def _excel_doc(doc_id="doc-1", name="품질실적.xlsx"):
    return {
        "id": doc_id,
        "filename": name,
        "stored_path": f"/uploads/{name}",
        "raw_text": f"[tabular file: {name}]",
    }


def _state(message, docs=None, **extra):
    docs = docs if docs is not None else [_excel_doc()]
    state = {
        "user_message": message,
        "documents": docs,
        "document": docs[0] if docs else {"raw_text": ""},
        "actions": [],
        "intent": "",
        "plan": {},
        "pending_clarification": None,
        "conversation_history": [],
        "execution_results": [],
    }
    state.update(extra)
    return state


def _execute_hint(**overrides):
    data = dict(
        intent="execute",
        goal="현재 제조 데이터 상태 확인",
        requires_tool=True,
        suggested_capability="tabular_data_analysis",
        confidence=0.88,
        reason="세션에 Excel이 있고 상태 확인 요청",
        clarification="",
    )
    data.update(overrides)
    return SemanticIntent(**data)


class HybridIntentTests(unittest.TestCase):
    def _route(self, message, docs=None, semantic=None, expect_llm=None, **extra):
        state = _state(message, docs, **extra)
        with patch(
            "app.agent.semantic_intent.call_semantic_intent",
            return_value=semantic or _execute_hint(),
        ) as mocked:
            resolved = resolve_hybrid_intent(state)
            if expect_llm is False:
                mocked.assert_not_called()
            elif expect_llm is True:
                mocked.assert_called()
        state.update(resolved)
        plan = build_work_plan(state)
        state["plan"] = plan.model_dump()
        route = after_plan(state)
        tools = []
        if resolved.get("intent") == "execute" and route == "tools":
            tools = [c.get("tool") for c in (tool_selector(state).get("tool_calls") or [])]
        return resolved, plan, tools

    def test_look_at_this_with_excel(self):
        resolved, _, tools = self._route("이거 좀 봐줘", semantic=_execute_hint(), expect_llm=True)
        self.assertEqual(resolved["intent"], "execute")
        self.assertEqual(resolved["intent_source"], "semantic")
        self.assertIn("analyze_excel", tools)

    def test_worse_than_before(self):
        resolved, _, tools = self._route("지난번보다 별로인 것 같은데?", semantic=_execute_hint(), expect_llm=True)
        self.assertEqual(resolved["intent"], "execute")
        self.assertIn("analyze_excel", tools)

    def test_machine_status(self):
        resolved, plan, tools = self._route("2호기 상태 어때?")
        self.assertEqual(resolved["intent"], "execute")
        self.assertIn("analyze_excel", tools)
        self.assertEqual(plan.steps[0].args.get("filter"), {"role": "equipment", "value": "2호기"})
        self.assertNotIn("generate_report", [s.tool for s in plan.steps])

    def test_item_feels_odd(self):
        _, _, tools = self._route("A품목 요즘 이상한 것 같은데", semantic=_execute_hint())
        self.assertIn("analyze_excel", tools)

    def test_where_is_worst(self):
        resolved, _, tools = self._route("어디가 제일 안 좋아?", semantic=_execute_hint())
        self.assertEqual(resolved["intent"], "execute")
        self.assertIn("analyze_excel", tools)

    def test_different_from_last_week(self):
        _, _, tools = self._route("지난주랑 좀 달라?", semantic=_execute_hint())
        self.assertIn("analyze_excel", tools)

    def test_hello_uses_rule_fast_path(self):
        resolved, _, tools = self._route("안녕", expect_llm=False)
        self.assertEqual(resolved["intent"], "query")
        self.assertEqual(resolved["intent_source"], "rule")
        self.assertNotIn("analyze_excel", tools)
        self.assertEqual(classify_rule_intent(_state("안녕"))["verdict"], "CONFIDENT_QUERY")

    def test_definition_uses_rule_fast_path(self):
        resolved, _, tools = self._route("불량률이 뭐야?", expect_llm=False)
        self.assertEqual(resolved["intent"], "query")
        self.assertEqual(resolved["intent_source"], "rule")
        self.assertNotIn("analyze_excel", tools)

    def test_look_at_this_without_excel_does_not_run_tool(self):
        resolved, plan, tools = self._route(
            "이거 좀 봐줘",
            docs=[],
            semantic=_execute_hint(confidence=0.88),
            expect_llm=True,
        )
        self.assertNotIn("analyze_excel", tools)
        self.assertTrue(plan.needs_clarification or resolved["intent"] == "query")

    def test_multiple_excel_asks_which_file(self):
        docs = [_excel_doc("a", "a.xlsx"), _excel_doc("b", "b.csv")]
        resolved, plan, tools = self._route("이거 좀 봐줘", docs=docs, semantic=_execute_hint())
        self.assertEqual(resolved["intent"], "execute")
        self.assertTrue(plan.needs_clarification)
        self.assertNotIn("analyze_excel", tools)
        self.assertIn("a.xlsx", plan.clarification)

    def test_low_confidence_does_not_run_tool(self):
        resolved, _, tools = self._route(
            "이거 괜찮은 거 맞아?",
            semantic=_execute_hint(confidence=0.31, clarification="어떤 내용을 확인해드릴까요?"),
        )
        self.assertEqual(resolved["intent"], "query")
        self.assertLess(resolved["intent_confidence"], 0.6)
        self.assertFalse(resolved["requires_tool"])
        self.assertNotIn("analyze_excel", tools)
        self.assertTrue(resolved["intent_clarification"])

    def test_explicit_excel_analysis_stays_on_rule_path(self):
        resolved, _, tools = self._route("이 엑셀 불량 현황 분석해줘", expect_llm=False)
        self.assertEqual(resolved["intent_source"], "rule")
        self.assertEqual(resolved["intent"], "execute")
        self.assertIn("analyze_excel", tools)

    def test_context_metadata_has_no_raw_rows(self):
        from app.agent.semantic_intent import build_intent_context

        ctx = build_intent_context(
            _state(
                "2호기 요즘 왜 이래?",
                conversation_history=[{"role": "user", "content": "x" * 500}],
                execution_results=[
                    {
                        "tool": "analyze_excel",
                        "data": {"schema": {"columns": ["품목", "설비"]}},
                    }
                ],
            )
        )
        blob = str(ctx)
        self.assertTrue(ctx["has_tabular"])
        self.assertIn("품질실적.xlsx", blob)
        self.assertLessEqual(len(ctx["recent_turns"][0]["content"]), 200)
        self.assertNotIn("stored_path", blob)
        self.assertEqual(ctx["known_schema_columns"], ["품목", "설비"])


if __name__ == "__main__":
    unittest.main()
