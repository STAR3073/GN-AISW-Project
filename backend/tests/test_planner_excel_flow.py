from __future__ import annotations

import unittest

from app.agent.excel_reply import format_excel_reply
from app.agent.nodes.planner import build_work_plan, planner
from app.agent.nodes.tool_selector import tool_selector
from app.agent.router import after_plan, resolve_intent
from app.agent.semantic_intent import resolve_hybrid_intent


def _excel_doc(doc_id="doc-1", name="quality.xlsx"):
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
    }
    state.update(extra)
    return state


class ExcelEndToEndRoutingTests(unittest.TestCase):
    def _plan_tools(self, message, docs=None, **extra):
        state = _state(message, docs, **extra)
        resolved = resolve_hybrid_intent(state)
        state.update(resolved)
        intent = resolved.get("intent") or resolve_intent(state)
        plan = build_work_plan(state)
        state["plan"] = plan.model_dump()
        state["plan_steps"] = [s.model_dump() for s in plan.steps]
        route = after_plan(state)
        calls = []
        if intent == "execute" and route == "tools":
            calls = tool_selector(state).get("tool_calls") or []
        tools = [c.get("tool") for c in calls]
        return intent, plan, tools

    def test_excel_analyze_request(self):
        intent, plan, tools = self._plan_tools("이 엑셀 분석해줘")
        self.assertEqual(intent, "execute")
        self.assertIn("analyze_excel", tools)
        self.assertEqual(plan.steps[0].document_id, "doc-1")

    def test_defect_status_request(self):
        _, _, tools = self._plan_tools("불량 현황 분석해줘")
        self.assertIn("analyze_excel", tools)

    def test_item_defect_rate_request(self):
        _, plan, tools = self._plan_tools("품목별 불량률 알려줘")
        self.assertIn("analyze_excel", tools)
        self.assertNotIn("generate_report", [s.tool for s in plan.steps])
        self.assertNotIn("generate_report", tools)

    def test_quality_report_plans_analyze_then_report(self):
        intent, plan, tools = self._plan_tools("이번 주 품질 현황 보고서 만들어줘")
        self.assertEqual(intent, "execute")
        self.assertEqual([s.tool for s in plan.steps], ["analyze_excel", "generate_report"])
        self.assertEqual(plan.steps[1].args.get("depends_on"), "analyze_excel")
        self.assertEqual(tools, ["analyze_excel"])
        self.assertIn("보고서", plan.goal)

    def test_problem_request(self):
        _, _, tools = self._plan_tools("이 데이터에서 뭐가 제일 문제야?")
        self.assertIn("analyze_excel", tools)

    def test_wow_request(self):
        _, _, tools = self._plan_tools("지난주보다 안 좋아진 게 있어?")
        self.assertIn("analyze_excel", tools)

    def test_hello_does_not_run_excel_tool(self):
        intent, _, tools = self._plan_tools("안녕")
        self.assertEqual(intent, "query")
        self.assertNotIn("analyze_excel", tools)

    def test_definition_does_not_run_excel_tool(self):
        intent, _, tools = self._plan_tools("불량률이 뭐야?")
        self.assertEqual(intent, "query")
        self.assertNotIn("analyze_excel", tools)

    def test_missing_excel_asks_upload(self):
        intent, plan, tools = self._plan_tools("불량 현황 분석해줘", docs=[])
        self.assertEqual(intent, "execute")
        self.assertTrue(plan.needs_clarification)
        self.assertEqual(after_plan({"plan": plan.model_dump()}), "reply")
        self.assertNotIn("analyze_excel", tools)
        self.assertIn("없", plan.clarification)

    def test_multiple_excel_asks_which_file(self):
        docs = [_excel_doc("a", "a.xlsx"), _excel_doc("b", "b.csv")]
        intent, plan, tools = self._plan_tools("불량 현황 분석해줘", docs=docs)
        self.assertEqual(intent, "execute")
        self.assertTrue(plan.needs_clarification)
        self.assertNotIn("analyze_excel", tools)
        self.assertIn("a.xlsx", plan.clarification)
        self.assertIn("b.csv", plan.clarification)

    def test_clarification_reply_lists_candidates(self):
        text = format_excel_reply(
            {
                "ok": True,
                "needs_clarification": True,
                "clarification": {
                    "field": "defect_qty",
                    "message": "불량수량으로 사용할 컬럼을 확정할 수 없습니다.",
                    "candidates": ["NG_QTY", "검사불량", "최종NG"],
                },
            }
        )
        self.assertIn("NG_QTY", text)
        self.assertIn("검사불량", text)
        self.assertIn("최종NG", text)
        self.assertIn("1.", text)

    def test_column_answer_resumes_analyze_excel(self):
        pending = {
            "type": "column",
            "tool": "analyze_excel",
            "document_id": "doc-1",
            "field": "defect_qty",
            "candidates": ["NG_QTY", "검사불량", "최종NG"],
        }
        intent, plan, tools = self._plan_tools("최종NG", pending_clarification=pending)
        self.assertEqual(intent, "execute")
        self.assertIn("analyze_excel", tools)
        self.assertEqual(plan.steps[0].args.get("column_mapping"), {"defect_qty": "최종NG"})

    def test_prose_document_analysis_stays_ingest(self):
        docs = [
            {
                "id": "pdf-1",
                "filename": "공고문.pdf",
                "raw_text": "2026 경남 AI 경진대회 공고문입니다. 제출 서류를 확인하세요.",
            }
        ]
        intent = resolve_intent(_state("이 문서 분석해서 해야 할 일 정리하고 진행해줘", docs))
        self.assertEqual(intent, "ingest")

    def test_planner_node_step_name(self):
        out = planner(_state("이 엑셀 분석해줘"))
        self.assertEqual(out["current_step"], "planner")
        self.assertTrue(out["plan"]["requires_tools"])

    def test_upload_pending_resumes_when_file_is_linked(self):
        pending = {
            "type": "upload",
            "tool": "analyze_excel",
            "candidates": [],
            "message": "파일을 업로드해 주세요.",
            "include_report": True,
        }
        intent, plan, tools = self._plan_tools(
            "이번 주 품질 현황 보고서 만들어줘",
            pending_clarification=pending,
        )
        self.assertEqual(intent, "execute")
        self.assertFalse(plan.needs_clarification)
        self.assertEqual([s.tool for s in plan.steps], ["analyze_excel", "generate_report"])
        self.assertIn("analyze_excel", tools)
        out = planner(_state("이번 주 품질 현황 보고서 만들어줘", pending_clarification=pending))
        self.assertIsNone(out["pending_clarification"])

    def test_upload_pending_without_file_still_asks(self):
        pending = {
            "type": "upload",
            "tool": "analyze_excel",
            "candidates": [],
            "message": "파일을 업로드해 주세요.",
            "include_report": True,
        }
        _, plan, tools = self._plan_tools(
            "이번 주 품질 현황 보고서 만들어줘",
            docs=[],
            pending_clarification=pending,
        )
        self.assertTrue(plan.needs_clarification)
        self.assertNotIn("analyze_excel", tools)

    def test_tabular_detected_from_stored_path(self):
        docs = [
            {
                "id": "doc-stored",
                "filename": "품질실적",
                "stored_path": "/uploads/acef2b90.xlsx",
                "raw_text": "[tabular file: acef2b90.xlsx]",
            }
        ]
        _, plan, tools = self._plan_tools("이번 주 품질 현황 보고서 만들어줘", docs=docs)
        self.assertFalse(plan.needs_clarification)
        self.assertIn("analyze_excel", tools)
        self.assertEqual(plan.steps[0].document_id, "doc-stored")


class SessionDocumentReplaceTests(unittest.TestCase):
    def test_empty_document_ids_do_not_replace(self):
        from app.api.sessions import _incoming_document_ids, _replace_documents

        self.assertEqual(_incoming_document_ids(None), [])
        self.assertEqual(_incoming_document_ids([]), [])
        self.assertEqual(_incoming_document_ids(["", "  ", "doc-1", "doc-1"]), ["doc-1"])

        class _Session:
            document_id = "keep-me"
            document_links = ["existing"]

        session = _Session()
        _replace_documents(None, session, [])
        self.assertEqual(session.document_id, "keep-me")
        self.assertEqual(session.document_links, ["existing"])


if __name__ == "__main__":
    unittest.main()
