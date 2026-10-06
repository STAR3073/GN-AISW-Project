from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.agent.excel_reply import (
    explicit_column_mapping_from_state,
    pending_from_excel,
    pending_from_state,
)
from app.agent.nodes.planner import build_work_plan, planner
from app.agent.nodes.tool_selector import tool_selector
from app.agent.router import after_plan
from app.agent.work_intent import match_clarification_choice, match_clarification_choice_rules
from app.tools.excel_analyzer import inspect_and_analyze


CANDIDATES = ["품목명", "품목코드"]


def _pending(**extra):
    data = {
        "type": "column",
        "tool": "analyze_excel",
        "document_id": "doc-1",
        "field": "item",
        "candidates": list(CANDIDATES),
        "message": "품목으로 사용할 컬럼을 확정할 수 없습니다.",
    }
    data.update(extra)
    return data


def _state(message, pending=None, **extra):
    docs = extra.pop(
        "documents",
        [
            {
                "id": "doc-1",
                "filename": "품질실적.xlsx",
                "stored_path": "/uploads/품질실적.xlsx",
                "raw_text": "[tabular file: 품질실적.xlsx]",
            }
        ],
    )
    state = {
        "user_message": message,
        "intent": "execute",
        "documents": docs,
        "document": docs[0] if docs else {},
        "pending_clarification": pending,
        "actions": [],
        "plan": {},
    }
    state.update(extra)
    return state


class ClarificationChoiceTests(unittest.TestCase):
    def _pick(self, message):
        with patch("app.agent.work_intent._semantic_clarification_choice") as mocked:
            picked = match_clarification_choice(message, CANDIDATES)
            mocked.assert_not_called()
        return picked

    def test_number_and_name_variants(self):
        cases = {
            "2": "품목코드",
            "2번": "품목코드",
            "2 번": "품목코드",
            "품목코드": "품목코드",
            "품목 코드": "품목코드",
            "품목 코드를 기준으로": "품목코드",
            "품목 코드를 기준으로 해서": "품목코드",
            "품목명으로 해줘": "품목명",
            "두 번째": "품목코드",
            "두번째 거": "품목코드",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(self._pick(message), expected)
                self.assertEqual(match_clarification_choice_rules(message, CANDIDATES), expected)

    def test_ambiguous_does_not_pick(self):
        for message in ("둘 다", "아무거나", "품목명이나 품목코드 아무거나"):
            with self.subTest(message=message):
                with patch("app.agent.work_intent._semantic_clarification_choice") as mocked:
                    self.assertIsNone(match_clarification_choice(message, CANDIDATES))
                    mocked.assert_not_called()

    def test_uncertain_keeps_original_pending(self):
        pending = _pending(resolved_mapping={"item": "품목명"}, field="defect_qty", candidates=["불량수량", "불량률"])
        with patch("app.agent.work_intent._semantic_clarification_choice", return_value=None):
            plan = build_work_plan(_state("잘 모르겠어", pending))
        self.assertTrue(plan.needs_clarification)
        self.assertFalse(plan.requires_tools)
        self.assertEqual(plan.pending["type"], "column")
        self.assertEqual(plan.pending["field"], "defect_qty")
        self.assertEqual(plan.pending["candidates"], ["불량수량", "불량률"])
        self.assertEqual(plan.pending["document_id"], "doc-1")
        self.assertEqual(plan.pending["resolved_mapping"], {"item": "품목명"})
        out = planner(_state("잘 모르겠어", pending))
        self.assertEqual(out["pending_clarification"]["type"], "column")
        self.assertEqual(out["pending_clarification"]["resolved_mapping"], {"item": "품목명"})
        self.assertNotEqual(out["pending_clarification"].get("type"), "upload")

    def test_natural_phrase_maps_original_column_name(self):
        pending = _pending()
        with patch("app.agent.work_intent._semantic_clarification_choice") as mocked:
            plan = build_work_plan(_state("품목 코드를 기준으로 해서", pending))
            mocked.assert_not_called()
        self.assertFalse(plan.needs_clarification)
        self.assertEqual(plan.steps[0].args["column_mapping"], {"item": "품목코드"})
        self.assertEqual(plan.steps[0].args["document_id"], "doc-1")

    def test_semantic_fallback_only_when_rules_fail(self):
        with patch(
            "app.agent.work_intent._semantic_clarification_choice",
            return_value="품목코드",
        ) as mocked:
            picked = match_clarification_choice("이름 말고 코드로 보는 게 낫겠어", CANDIDATES)
        mocked.assert_called_once()
        self.assertEqual(picked, "품목코드")

    def test_low_confidence_semantic_does_not_pick(self):
        with patch("app.agent.semantic_intent.resolve_semantic_clarification", return_value=None):
            self.assertIsNone(match_clarification_choice("글쎄 뭐가 맞을까", CANDIDATES))


class ClarificationExcelFlowTests(unittest.TestCase):
    def test_item_ambiguity_then_natural_choice_reruns_and_clears(self):
        rows = [
            {"품목명": "BRACKET", "품목코드": "P-01", "생산수량": 100, "불량수량": 4},
            {"품목명": "HOUSING", "품목코드": "P-02", "생산수량": 80, "불량수량": 2},
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "quality.xlsx"
            pd.DataFrame(rows).to_excel(path, index=False, sheet_name="품질")
            first = inspect_and_analyze(path, filename="quality.xlsx", document_id="doc-1")
        self.assertTrue(first["needs_clarification"])
        self.assertEqual(first["clarification"]["field"], "item")
        self.assertIn("품목명", first["clarification"]["candidates"])
        self.assertIn("품목코드", first["clarification"]["candidates"])

        pending = pending_from_excel(first)
        self.assertEqual(pending["type"], "column")
        self.assertEqual(pending["field"], "item")
        self.assertEqual(pending["document_id"], "doc-1")

        state = _state("품목 코드를 기준으로 해서", pending)
        with patch("app.agent.work_intent._semantic_clarification_choice") as mocked:
            plan = build_work_plan(state)
            mocked.assert_not_called()
        self.assertEqual(plan.steps[0].args.get("column_mapping"), {"item": "품목코드"})
        state["plan"] = plan.model_dump()
        state["plan_steps"] = [s.model_dump() for s in plan.steps]
        self.assertEqual(after_plan(state), "tools")
        calls = tool_selector({**state, "intent": "execute"}).get("tool_calls") or []
        self.assertEqual(calls[0]["args"]["column_mapping"], {"item": "품목코드"})

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "quality.xlsx"
            pd.DataFrame(rows).to_excel(path, index=False, sheet_name="품질")
            second = inspect_and_analyze(
                path,
                filename="quality.xlsx",
                document_id="doc-1",
                column_overrides={"item": "품목코드"},
            )
        self.assertFalse(second["needs_clarification"])
        self.assertEqual(second["column_mapping"]["item"]["column"], "품목코드")
        self.assertIsNone(pending_from_excel(second))

        out = planner(state)
        self.assertIsNone(out["pending_clarification"])


class MultiStepClarificationTests(unittest.TestCase):
    def _two_step_rows(self):
        return [
            {"품목명": "BRACKET", "품목코드": "P-01", "생산수량": 100, "불량수량": 4, "검사불량": 3},
            {"품목명": "HOUSING", "품목코드": "P-02", "생산수량": 80, "불량수량": 2, "검사불량": 1},
        ]

    def _three_step_rows(self):
        return [
            {
                "품목명": "BRACKET",
                "품목코드": "P-01",
                "생산수량": 100,
                "생산량": 100,
                "불량수량": 4,
                "검사불량": 3,
            },
            {
                "품목명": "HOUSING",
                "품목코드": "P-02",
                "생산수량": 80,
                "생산량": 80,
                "불량수량": 2,
                "검사불량": 1,
            },
        ]

    def _analyze(self, rows, overrides=None):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "quality.xlsx"
            pd.DataFrame(rows).to_excel(path, index=False, sheet_name="품질")
            return inspect_and_analyze(
                path,
                filename="quality.xlsx",
                document_id="doc-1",
                column_overrides=overrides or {},
            )

    def test_item_then_defect_qty_merges_mapping(self):
        first = self._analyze(self._two_step_rows())
        self.assertEqual(first["clarification"]["field"], "item")
        pending = pending_from_excel(first)
        self.assertEqual(pending.get("resolved_mapping"), {})

        plan = build_work_plan(_state("1", pending))
        self.assertEqual(plan.steps[0].args["column_mapping"], {"item": "품목명"})

        second = self._analyze(self._two_step_rows(), {"item": "품목명"})
        self.assertTrue(second["needs_clarification"])
        self.assertEqual(second["clarification"]["field"], "defect_qty")
        self.assertIn("불량수량", second["clarification"]["candidates"])

        next_pending = pending_from_state(
            {
                "execution_results": [{"tool": "analyze_excel", "ok": True, "data": second}],
                "tool_calls": [
                    {
                        "tool": "analyze_excel",
                        "args": {"document_id": "doc-1", "column_mapping": {"item": "품목명"}},
                    }
                ],
                "pending_clarification": None,
                "plan": {},
            }
        )
        self.assertEqual(next_pending["field"], "defect_qty")
        self.assertEqual(next_pending["resolved_mapping"], {"item": "품목명"})

        plan2 = build_work_plan(_state("1", next_pending))
        mapping = plan2.steps[0].args["column_mapping"]
        self.assertEqual(mapping, {"item": "품목명", "defect_qty": "불량수량"})
        state = _state("1", next_pending)
        state["plan"] = plan2.model_dump()
        calls = tool_selector({**state, "intent": "execute"}).get("tool_calls") or []
        self.assertEqual(calls[0]["args"]["column_mapping"], {"item": "품목명", "defect_qty": "불량수량"})

        final = self._analyze(self._two_step_rows(), mapping)
        self.assertFalse(final["needs_clarification"])
        self.assertIsNone(pending_from_excel(final))
        self.assertEqual(final["column_mapping"]["item"]["column"], "품목명")
        self.assertEqual(final["column_mapping"]["defect_qty"]["column"], "불량수량")
        out = planner(_state("1", next_pending))
        self.assertIsNone(out["pending_clarification"])

    def test_three_step_item_defect_production(self):
        rows = self._three_step_rows()
        first = self._analyze(rows)
        item_plan = build_work_plan(_state("1", pending_from_excel(first)))
        self.assertEqual(item_plan.steps[0].args["column_mapping"], {"item": "품목명"})

        second = self._analyze(rows, {"item": "품목명"})
        defect_pending = pending_from_excel(second, resolved_mapping={"item": "품목명"})
        self.assertEqual(defect_pending["field"], "defect_qty")
        self.assertEqual(defect_pending["resolved_mapping"], {"item": "품목명"})
        defect_plan = build_work_plan(_state("1", defect_pending))
        self.assertEqual(
            defect_plan.steps[0].args["column_mapping"],
            {"item": "품목명", "defect_qty": "불량수량"},
        )

        third = self._analyze(rows, {"item": "품목명", "defect_qty": "불량수량"})
        self.assertTrue(third["needs_clarification"])
        self.assertEqual(third["clarification"]["field"], "production_qty")
        prod_pending = pending_from_excel(
            third,
            resolved_mapping={"item": "품목명", "defect_qty": "불량수량"},
        )
        self.assertEqual(prod_pending["resolved_mapping"], {"item": "품목명", "defect_qty": "불량수량"})
        prod_plan = build_work_plan(_state("1", prod_pending))
        mapping = prod_plan.steps[0].args["column_mapping"]
        self.assertEqual(mapping["item"], "품목명")
        self.assertEqual(mapping["defect_qty"], "불량수량")
        self.assertIn("production_qty", mapping)
        self.assertEqual(len(mapping), 3)

        final = self._analyze(rows, mapping)
        self.assertFalse(final["needs_clarification"])
        self.assertIsNone(pending_from_excel(final))
        out = planner(_state("1", prod_pending))
        self.assertIsNone(out["pending_clarification"])

    def test_failed_choice_keeps_resolved_mapping(self):
        pending = _pending(
            field="defect_qty",
            candidates=["불량수량", "불량률"],
            resolved_mapping={"item": "품목명"},
        )
        with patch("app.agent.work_intent._semantic_clarification_choice", return_value=None):
            plan = build_work_plan(_state("잘 모르겠어", pending))
            out = planner(_state("잘 모르겠어", pending))
        self.assertEqual(plan.pending["resolved_mapping"], {"item": "품목명"})
        self.assertEqual(out["pending_clarification"]["resolved_mapping"], {"item": "품목명"})
        self.assertEqual(out["pending_clarification"]["field"], "defect_qty")

    def test_merge_adds_role_and_update_replaces_same_role(self):
        pending = _pending(resolved_mapping={"item": "품목명"}, field="defect_qty", candidates=["불량수량", "불량률"])
        merged = build_work_plan(_state("1", pending))
        self.assertEqual(merged.steps[0].args["column_mapping"], {"item": "품목명", "defect_qty": "불량수량"})

        update = build_work_plan(_state("2", _pending(resolved_mapping={"item": "품목명"})))
        self.assertEqual(update.steps[0].args["column_mapping"], {"item": "품목코드"})

    def test_other_document_does_not_inherit_mapping(self):
        mapping = explicit_column_mapping_from_state(
            {
                "pending_clarification": {
                    "type": "column",
                    "document_id": "doc-a",
                    "resolved_mapping": {"item": "품목명"},
                },
                "tool_calls": [
                    {
                        "tool": "analyze_excel",
                        "args": {"document_id": "doc-a", "column_mapping": {"item": "품목명", "defect_qty": "불량수량"}},
                    }
                ],
                "plan": {},
            },
            "doc-b",
        )
        self.assertEqual(mapping, {})
        other = build_work_plan(
            _state(
                "이 엑셀 분석해줘",
                None,
                documents=[
                    {
                        "id": "doc-b",
                        "filename": "other.xlsx",
                        "stored_path": "/uploads/other.xlsx",
                        "raw_text": "[tabular file: other.xlsx]",
                    }
                ],
            )
        )
        self.assertEqual((other.steps[0].args or {}).get("column_mapping"), None)

        payload = {
            "needs_clarification": True,
            "file": {"id": "doc-b"},
            "clarification": {"field": "item", "candidates": ["품목명", "품목코드"], "message": "컬럼을 선택해 주세요."},
        }
        isolated = pending_from_excel(payload, resolved_mapping={"item": "품목명"}, source_document_id="doc-a")
        self.assertEqual(isolated["resolved_mapping"], {})
        self.assertEqual(isolated["document_id"], "doc-b")

    def test_success_clears_pending(self):
        final = self._analyze(self._two_step_rows(), {"item": "품목명", "defect_qty": "불량수량"})
        self.assertFalse(final["needs_clarification"])
        pending = pending_from_state(
            {
                "execution_results": [{"tool": "analyze_excel", "ok": True, "data": final}],
                "tool_calls": [
                    {
                        "tool": "analyze_excel",
                        "args": {
                            "document_id": "doc-1",
                            "column_mapping": {"item": "품목명", "defect_qty": "불량수량"},
                        },
                    }
                ],
                "pending_clarification": _pending(resolved_mapping={"item": "품목명"}),
                "plan": {"needs_clarification": False},
            }
        )
        self.assertIsNone(pending)
        self.assertIsNone(pending_from_excel(final))

    def test_does_not_freeze_auto_inferred_mapping(self):
        second = self._analyze(self._two_step_rows(), {"item": "품목명"})
        self.assertTrue(second["needs_clarification"])
        next_pending = pending_from_state(
            {
                "execution_results": [{"tool": "analyze_excel", "ok": True, "data": second}],
                "tool_calls": [
                    {
                        "tool": "analyze_excel",
                        "args": {"document_id": "doc-1", "column_mapping": {"item": "품목명"}},
                    }
                ],
                "pending_clarification": None,
                "plan": {
                    "steps": [
                        {
                            "tool": "analyze_excel",
                            "document_id": "doc-1",
                            "args": {"document_id": "doc-1", "column_mapping": {"item": "품목명"}},
                        }
                    ]
                },
            }
        )
        self.assertEqual(next_pending["resolved_mapping"], {"item": "품목명"})
        self.assertNotIn("production_qty", next_pending["resolved_mapping"])
        self.assertNotIn("date", next_pending["resolved_mapping"])

    def test_reask_keeps_resolved_mapping(self):
        pending = _pending(
            field="defect_qty",
            candidates=["불량수량", "불량률"],
            resolved_mapping={"item": "품목명"},
        )
        with patch("app.agent.work_intent._semantic_clarification_choice", return_value=None):
            plan = build_work_plan(_state("다시 알려줘", pending))
            out = planner(_state("다시 알려줘", pending))
        self.assertTrue(plan.needs_clarification)
        self.assertEqual(plan.pending["resolved_mapping"], {"item": "품목명"})
        self.assertEqual(plan.pending["field"], "defect_qty")
        self.assertEqual(out["pending_clarification"]["resolved_mapping"], {"item": "품목명"})
        self.assertEqual(out["pending_clarification"]["candidates"], ["불량수량", "불량률"])


if __name__ == "__main__":
    unittest.main()
