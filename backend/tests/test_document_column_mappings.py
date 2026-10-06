from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from app.agent.excel_reply import (
    pending_from_state,
    persistent_mapping_for_document,
)
from app.agent.nodes.planner import build_work_plan, planner
from app.api.sessions import _build_initial_state
from app.tools.excel_analyzer import inspect_and_analyze
from app.tools.registry import invoke_tool


def _xlsx(path: Path, rows: list[dict], sheet: str = "품질") -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, sheet_name=sheet)
    return path


def _item_equip_rows():
    return [
        {
            "품목명": "BRACKET",
            "품목코드": "P-01",
            "설비번호": "EQ-01",
            "설비명": "1호기",
            "설비검출횟수": 8,
            "생산수량": 100,
            "불량수량": 2,
            "생산일": "2026-08-03",
        },
        {
            "품목명": "HOUSING",
            "품목코드": "P-02",
            "설비번호": "EQ-02",
            "설비명": "2호기",
            "설비검출횟수": 3,
            "생산수량": 80,
            "불량수량": 1,
            "생산일": "2026-09-03",
        },
    ]


def _dim_rows():
    rows = []
    for row in _item_equip_rows():
        extra = dict(row)
        extra.update(
            {
                "품목불량수량": extra["불량수량"],
                "공정명": "용접" if extra["품목명"] == "BRACKET" else "가공",
                "공정코드": "W1" if extra["품목명"] == "BRACKET" else "M1",
                "공정불량건수": extra["불량수량"],
            }
        )
        rows.append(extra)
    return rows


def _doc(doc_id="doc-1", name="quality.xlsx"):
    return {
        "id": doc_id,
        "filename": name,
        "stored_path": f"/uploads/{name}",
        "raw_text": f"[tabular file: {name}]",
    }


def _pending(field, candidates, document_id="doc-1", **extra):
    data = {
        "type": "column",
        "tool": "analyze_excel",
        "document_id": document_id,
        "field": field,
        "candidates": candidates,
        "message": f"{field} 컬럼을 확정할 수 없습니다.",
    }
    data.update(extra)
    return data


def _state(message, *, docs=None, pending=None, **extra):
    docs = docs if docs is not None else [_doc()]
    state = {
        "user_message": message,
        "intent": "execute",
        "documents": docs,
        "document": docs[0] if docs else {},
        "pending_clarification": pending,
        "document_column_mappings": extra.pop("document_column_mappings", {}),
        "actions": [],
        "plan": extra.pop("plan", {}),
    }
    state.update(extra)
    return state


class PersistentColumnMappingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _analyze(self, rows=None, overrides=None, document_id="doc-1"):
        path = _xlsx(self.root / f"q-{uuid4().hex}.xlsx", rows or _item_equip_rows())
        return inspect_and_analyze(
            path,
            filename="quality.xlsx",
            document_id=document_id,
            column_overrides=overrides or {},
        )

    def test_user_confirmed_mapping_is_reused_on_next_question(self):
        first = self._analyze()
        self.assertTrue(first["needs_clarification"])
        self.assertEqual(first["clarification"]["field"], "item")
        self.assertIn("품목명", first["clarification"]["candidates"])
        self.assertIn("품목코드", first["clarification"]["candidates"])

        item_out = planner(
            _state(
                "1",
                pending=_pending("item", ["품목명", "품목코드"]),
            )
        )
        self.assertEqual(
            item_out["document_column_mappings"]["doc-1"],
            {"item": "품목명"},
        )
        self.assertEqual(item_out["plan"]["steps"][0]["args"]["column_mapping"], {"item": "품목명"})

        after_item = self._analyze(overrides={"item": "품목명"})
        self.assertTrue(after_item["needs_clarification"])
        self.assertEqual(after_item["clarification"]["field"], "equipment")
        self.assertIn("설비번호", after_item["clarification"]["candidates"])
        self.assertIn("설비명", after_item["clarification"]["candidates"])
        self.assertNotIn("설비검출횟수", after_item["clarification"]["candidates"])

        equip_out = planner(
            _state(
                "1",
                pending=_pending(
                    "equipment",
                    ["설비번호", "설비명"],
                    resolved_mapping={"item": "품목명"},
                ),
                document_column_mappings={"doc-1": {"item": "품목명"}},
            )
        )
        self.assertEqual(
            equip_out["document_column_mappings"]["doc-1"],
            {"item": "품목명", "equipment": "설비번호"},
        )

        confirmed = {"item": "품목명", "equipment": "설비번호"}
        success = self._analyze(overrides=confirmed)
        self.assertFalse(success["needs_clarification"])
        self.assertEqual(success["column_mapping"]["item"]["column"], "품목명")
        self.assertEqual(success["column_mapping"]["equipment"]["column"], "설비번호")
        pending = pending_from_state(
            {
                "execution_results": [{"tool": "analyze_excel", "ok": True, "data": success}],
                "document_column_mappings": {"doc-1": confirmed},
                "plan": {"needs_clarification": False, "steps": [{"tool": "analyze_excel", "args": {"column_mapping": confirmed}}]},
            }
        )
        self.assertIsNone(pending)
        self.assertEqual(
            persistent_mapping_for_document({"document_column_mappings": {"doc-1": confirmed}}, "doc-1"),
            confirmed,
        )

        reuse = build_work_plan(
            _state(
                "뭐가 제일 문제야?",
                document_column_mappings={"doc-1": confirmed},
            )
        )
        self.assertEqual(reuse.steps[0].args.get("column_mapping"), confirmed)
        reused = self._analyze(overrides=reuse.steps[0].args["column_mapping"])
        self.assertFalse(reused["needs_clarification"])
        self.assertNotEqual((reused.get("clarification") or {}).get("field"), "item")
        self.assertNotEqual((reused.get("clarification") or {}).get("field"), "equipment")

    def test_same_document_other_question_reuses_mapping(self):
        mappings = {"doc-1": {"item": "품목명", "equipment": "설비번호"}}
        plan = build_work_plan(_state("품목별 불량률 알려줘", document_column_mappings=mappings))
        self.assertEqual(plan.steps[0].args.get("column_mapping"), mappings["doc-1"])

    def test_other_document_does_not_receive_mapping(self):
        mappings = {"doc-1": {"item": "품목명", "equipment": "설비번호"}}
        plan = build_work_plan(
            _state(
                "품목별 불량률 알려줘",
                docs=[_doc("doc-b", "other.xlsx")],
                document_column_mappings=mappings,
            )
        )
        self.assertNotIn("column_mapping", plan.steps[0].args)

    def test_new_selection_updates_same_role(self):
        out = planner(
            _state(
                "품목코드",
                pending=_pending(
                    "item",
                    ["품목명", "품목코드"],
                    resolved_mapping={"item": "품목명", "equipment": "설비번호"},
                ),
                document_column_mappings={"doc-1": {"item": "품목명", "equipment": "설비번호"}},
            )
        )
        self.assertEqual(
            out["document_column_mappings"]["doc-1"],
            {"item": "품목코드", "equipment": "설비번호"},
        )
        self.assertEqual(out["plan"]["steps"][0]["args"]["column_mapping"]["item"], "품목코드")

    def test_auto_inferred_mapping_is_not_persisted(self):
        out = planner(_state("1", pending=_pending("item", ["품목명", "품목코드"])))
        stored = out["document_column_mappings"]["doc-1"]
        self.assertEqual(stored, {"item": "품목명"})
        analyzed = self._analyze(overrides=stored)
        self.assertIsNotNone(analyzed["column_mapping"]["production_qty"]["column"])
        self.assertNotIn("production_qty", stored)
        self.assertNotIn("date", stored)

    def test_pending_clear_keeps_persistent_mapping(self):
        confirmed = {"item": "품목명", "equipment": "설비번호"}
        success = self._analyze(overrides=confirmed)
        self.assertFalse(success["needs_clarification"])
        pending = pending_from_state(
            {
                "execution_results": [{"tool": "analyze_excel", "ok": True, "data": success}],
                "document_column_mappings": {"doc-1": confirmed},
                "plan": {"needs_clarification": False},
            }
        )
        self.assertIsNone(pending)
        self.assertEqual(
            persistent_mapping_for_document({"document_column_mappings": {"doc-1": confirmed}}, "doc-1"),
            confirmed,
        )

    def test_next_turn_restores_persistent_mapping(self):
        session = SimpleNamespace(
            id="sess-1",
            state_json={"document_column_mappings": {"doc-1": {"item": "품목명", "equipment": "설비번호"}}},
            document_id=None,
            actions=[],
            messages=[],
        )
        restored = _build_initial_state(session, [], "뭐가 제일 문제야?")
        self.assertEqual(
            restored["document_column_mappings"]["doc-1"],
            {"item": "품목명", "equipment": "설비번호"},
        )
        self.assertEqual(restored["tool_calls"], [])
        self.assertEqual(restored["execution_results"], [])
        self.assertEqual(restored["validated_facts"], {})

    def test_stale_column_is_dropped_and_rechecked(self):
        result = self._analyze(overrides={"item": "없는컬럼", "equipment": "설비번호"})
        self.assertIn("item", result.get("stale_overrides") or [])
        self.assertNotEqual(result["column_mapping"]["item"]["column"], "없는컬럼")
        self.assertEqual(result["column_mapping"]["equipment"]["column"], "설비번호")
        if result.get("needs_clarification"):
            self.assertEqual(result["clarification"]["field"], "item")

        stale_state = {
            "document_column_mappings": {"doc-1": {"item": "없는컬럼", "equipment": "설비번호"}},
        }
        with patch("app.tools.handlers.run_analyze_excel", return_value=result):
            invoked = invoke_tool(
                "analyze_excel",
                {"document_id": "doc-1", "column_mapping": {"item": "없는컬럼", "equipment": "설비번호"}},
                stale_state,
            )
        pruned = invoked.state_updates["document_column_mappings"]["doc-1"]
        self.assertNotIn("item", pruned)
        self.assertEqual(pruned.get("equipment"), "설비번호")


class DimensionMetricExclusionTests(unittest.TestCase):
    def _mapping(self, rows):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / f"dim-{uuid4().hex}.xlsx", rows)
            return inspect_and_analyze(path, filename="dim.xlsx", document_id="doc-1")

    def test_equipment_excludes_detection_count(self):
        result = self._mapping(_dim_rows())
        equipment = result["column_mapping"]["equipment"]
        self.assertIn("설비번호", equipment["candidates"])
        self.assertIn("설비명", equipment["candidates"])
        self.assertNotIn("설비검출횟수", equipment["candidates"])
        self.assertNotEqual(equipment["column"], "설비검출횟수")

    def test_item_excludes_qty_column(self):
        result = self._mapping(_dim_rows())
        item = result["column_mapping"]["item"]
        self.assertIn("품목명", item["candidates"])
        self.assertIn("품목코드", item["candidates"])
        self.assertNotIn("품목불량수량", item["candidates"])

    def test_process_excludes_count_column(self):
        result = self._mapping(_dim_rows())
        process = result["column_mapping"]["process"]
        self.assertIn("공정명", process["candidates"])
        self.assertIn("공정코드", process["candidates"])
        self.assertNotIn("공정불량건수", process["candidates"])


if __name__ == "__main__":
    unittest.main()
