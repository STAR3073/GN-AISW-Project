from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from openpyxl import Workbook

from app.agent.nodes.tool_executor import tool_executor
from app.tools.excel_analyzer import inspect_and_analyze
from app.tools.registry import invoke_tool, is_registered


def _xlsx(path: Path, rows: list[dict], sheet: str = "품질") -> Path:
    pd.DataFrame(rows).to_excel(path, index=False, sheet_name=sheet)
    return path


def _quality_rows() -> list[dict]:
    return [
        {"품목": "BRACKET", "공정": "용접", "설비": "W-01", "생산일": "2026-08-03", "생산수량": 100, "불량수량": 2, "불량유형": "기공"},
        {"품목": "BRACKET", "공정": "용접", "설비": "W-01", "생산일": "2026-09-03", "생산수량": 100, "불량수량": 12, "불량유형": "기공"},
        {"품목": "HOUSING", "공정": "가공", "설비": "M-02", "생산일": "2026-08-03", "생산수량": 80, "불량수량": 1, "불량유형": "치수"},
        {"품목": "HOUSING", "공정": "가공", "설비": "M-02", "생산일": "2026-09-03", "생산수량": 80, "불량수량": 1, "불량유형": "외관"},
    ]


class _Settings:
    def __init__(self, root: Path):
        self.upload_dir = root
        self.convert_dir = root / "converted"
        self.convert_dir.mkdir(exist_ok=True)
        self.max_upload_bytes = 20 * 1024 * 1024


class AnalyzeExcelTests(unittest.TestCase):
    def test_normal_manufacturing_quality_xlsx(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "quality.xlsx", _quality_rows())
            result = inspect_and_analyze(path, filename="quality.xlsx")
        self.assertTrue(result["ok"])
        self.assertFalse(result["needs_clarification"])
        self.assertEqual(result["schema"]["row_count"], 4)
        self.assertIn("품목", result["schema"]["columns"])
        self.assertEqual(result["column_mapping"]["item"]["column"], "품목")
        self.assertEqual(result["column_mapping"]["defect_qty"]["column"], "불량수량")
        self.assertEqual(result["column_mapping"]["production_qty"]["column"], "생산수량")
        self.assertIn("품질", [s["name"] for s in result["schema"]["sheets"]])
        metrics = result["quality_metrics"]
        self.assertEqual(metrics["production_qty_total"], 360)
        self.assertEqual(metrics["defect_qty_total"], 16)
        self.assertAlmostEqual(metrics["defect_rate_pct"], 16 / 360 * 100, places=2)
        self.assertEqual(metrics["formula"], "불량수량 / 생산수량 × 100")
        self.assertTrue(result["top_defects"])
        self.assertIn("by_item", result["group_analysis"])
        self.assertTrue(result["period_analysis"]["monthly"])

    def test_csv(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "quality.csv"
            pd.DataFrame(_quality_rows()).to_csv(path, index=False, encoding="utf-8-sig")
            result = inspect_and_analyze(path, filename="quality.csv")
        self.assertTrue(result["ok"])
        self.assertEqual(result["quality_metrics"]["defect_qty_total"], 16)
        self.assertEqual(result["file"]["suffix"], ".csv")

    def test_production_and_defect_qty_rate(self):
        rows = [
            {"품목명": "GEAR", "생산수량": 50, "불량수량": 5},
            {"품목명": "GEAR", "생산수량": 50, "불량수량": 0},
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "rate.xlsx", rows)
            result = inspect_and_analyze(path, filename="rate.xlsx")
        self.assertTrue(result["ok"])
        self.assertEqual(result["quality_metrics"]["production_qty_total"], 100)
        self.assertEqual(result["quality_metrics"]["defect_qty_total"], 5)
        self.assertEqual(result["quality_metrics"]["defect_rate_pct"], 5.0)

    def test_ok_ng_result_column(self):
        rows = [
            {"품목": "SHAFT", "판정": "OK", "검사일": "2026-09-01"},
            {"품목": "SHAFT", "판정": "NG", "검사일": "2026-09-01"},
            {"품목": "SHAFT", "판정": "합격", "검사일": "2026-09-02"},
            {"품목": "BUSH", "판정": "불합격", "검사일": "2026-09-02"},
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "okng.xlsx", rows)
            result = inspect_and_analyze(path, filename="okng.xlsx")
        self.assertTrue(result["ok"])
        metrics = result["quality_metrics"]
        self.assertEqual(metrics["ng_count"], 2)
        self.assertEqual(metrics["ok_count"], 2)
        self.assertEqual(metrics["inspection_fail_rate_pct"], 50)
        self.assertIsNone(metrics["defect_rate_pct"])
        self.assertIsNone(metrics["formula"])
        self.assertTrue(any("생산수량" in w or "불합격률" in w for w in result["warnings"]))

    def test_alternate_manufacturing_column_names(self):
        rows = [
            {"ITEM_NAME": "PIN", "PROD_QTY": 200, "NG_QTY": 4, "PROCESS": "조립", "DATE": "2026-09-10"},
            {"ITEM_NAME": "PIN", "PROD_QTY": 100, "NG_QTY": 1, "PROCESS": "조립", "DATE": "2026-09-11"},
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "en.xlsx", rows)
            result = inspect_and_analyze(path, filename="en.xlsx")
        self.assertTrue(result["ok"])
        self.assertFalse(result["needs_clarification"])
        self.assertEqual(result["column_mapping"]["item"]["column"], "ITEM_NAME")
        self.assertEqual(result["column_mapping"]["production_qty"]["column"], "PROD_QTY")
        self.assertEqual(result["column_mapping"]["defect_qty"]["column"], "NG_QTY")
        self.assertAlmostEqual(result["quality_metrics"]["defect_rate_pct"], 5 / 300 * 100, places=4)

    def test_ambiguous_defect_columns_need_clarification(self):
        rows = [
            {"품목": "COVER", "생산수량": 10, "NG_QTY": 1, "검사불량": 2, "최종NG": 3},
            {"품목": "COVER", "생산수량": 10, "NG_QTY": 0, "검사불량": 1, "최종NG": 1},
        ]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "ambig.xlsx", rows)
            result = inspect_and_analyze(path, filename="ambig.xlsx")
        self.assertTrue(result["ok"])
        self.assertTrue(result["needs_clarification"])
        self.assertEqual(result["column_mapping"]["defect_qty"]["status"], "ambiguous")
        candidates = result["column_mapping"]["defect_qty"]["candidates"]
        self.assertGreaterEqual(len(candidates), 2)
        self.assertTrue(any("NG_QTY" in candidates or c in {"NG_QTY", "검사불량", "최종NG"} for c in candidates))
        self.assertIsNotNone(result["clarification"])
        self.assertIn("불량수량", result["clarification"]["message"])

    def test_empty_sheet(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "empty.xlsx"
            Workbook().save(path)
            result = inspect_and_analyze(path, filename="empty.xlsx")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "empty_sheet")

    def test_unsupported_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("hello", encoding="utf-8")
            result = inspect_and_analyze(path, filename="note.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unsupported_type")

    def test_missing_file(self):
        result = inspect_and_analyze(Path("D:/tmp/does-not-exist-docaction.xlsx"), filename="missing.xlsx")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "file_not_found")

    def test_registry_invoke_and_executor(self):
        self.assertTrue(is_registered("analyze_excel"))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            path = _xlsx(root / "shop.xlsx", _quality_rows())
            settings = _Settings(root)
            state = {
                "session_id": "",
                "user_message": "이 파일의 불량 현황 분석해줘",
                "documents": [
                    {
                        "id": "doc-1",
                        "filename": "shop.xlsx",
                        "stored_path": str(path),
                    }
                ],
            }
            with patch("app.tools.excel_analyzer.get_settings", return_value=settings):
                result = invoke_tool("analyze_excel", {}, state)
                executed = tool_executor(
                    {
                        "actions": [],
                        "tool_calls": [{"tool": "analyze_excel", "args": {"document_id": "doc-1"}}],
                        "execution_results": [],
                        "documents": state["documents"],
                    }
                )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["quality_metrics"]["defect_qty_total"], 16)
        self.assertEqual(executed["current_step"], "tool_executor")
        self.assertTrue(executed["execution_results"][0]["ok"])
        self.assertEqual(executed["execution_results"][0]["tool"], "analyze_excel")

    def test_arbitrary_path_argument_is_ignored(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            outsider = Path(tmp) / "secret.xlsx"
            _xlsx(outsider, _quality_rows())
            result = invoke_tool("analyze_excel", {"path": str(outsider)}, {"documents": []})
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error"], "file_not_found")


class QtyRateMappingTests(unittest.TestCase):
    def _analyze(self, rows, overrides=None):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = _xlsx(Path(tmp) / "qty.xlsx", rows)
            return inspect_and_analyze(
                path,
                filename="qty.xlsx",
                document_id="doc-1",
                column_overrides=overrides or {},
            )

    def test_qty_and_rate_roles_map_separately(self):
        isolated = {
            "불량수량": "defect_qty",
            "불량률": "defect_rate",
            "NG수량": "defect_qty",
            "NG율": "defect_rate",
            "불량비율": "defect_rate",
            "생산수량": "production_qty",
            "검사수량": "inspection_qty",
            "투입수량": "input_qty",
            "양품수량": "good_qty",
            "OK수량": "good_qty",
        }
        for column, role in isolated.items():
            with self.subTest(column=column):
                result = self._analyze([{column: 1, "기타": "x"}])
                self.assertEqual(result["column_mapping"][role]["column"], column)
                self.assertEqual(result["column_mapping"][role]["status"], "mapped")
                if role != "defect_qty":
                    self.assertIsNone(result["column_mapping"]["defect_qty"]["column"])

    def test_defect_qty_candidates_exclude_rate_columns(self):
        rows = [
            {"품목": "A", "생산수량": 1000, "불량수량": 25, "불량률": 2.5},
            {"품목": "B", "생산수량": 1000, "불량수량": 25, "불량률": 2.5},
        ]
        result = self._analyze(rows)
        self.assertFalse(result["needs_clarification"])
        self.assertEqual(result["column_mapping"]["defect_qty"]["column"], "불량수량")
        self.assertEqual(result["column_mapping"]["defect_rate"]["column"], "불량률")
        self.assertNotIn("불량률", result["column_mapping"]["defect_qty"].get("candidates") or [])
        self.assertEqual(result["quality_metrics"]["defect_qty_total"], 50)
        self.assertEqual(result["quality_metrics"]["defect_rate_pct"], 2.5)
        self.assertEqual(result["quality_metrics"]["formula"], "불량수량 / 생산수량 × 100")
        self.assertEqual(result["quality_metrics"]["source_defect_rate"]["column"], "불량률")
        self.assertFalse(result["quality_metrics"]["source_defect_rate"]["aggregated"])
        self.assertFalse(result["quality_metrics"]["source_defect_rate"]["used_for_primary_metric"])

    def test_production_inspection_input_are_distinct_roles(self):
        rows = [{"생산수량": 100, "검사수량": 90, "투입수량": 110, "불량수량": 4}]
        result = self._analyze(rows)
        self.assertEqual(result["column_mapping"]["production_qty"]["column"], "생산수량")
        self.assertEqual(result["column_mapping"]["inspection_qty"]["column"], "검사수량")
        self.assertEqual(result["column_mapping"]["input_qty"]["column"], "투입수량")
        self.assertFalse(result["needs_clarification"])
        self.assertEqual(result["quality_metrics"]["production_qty_total"], 100)
        self.assertEqual(result["quality_metrics"]["inspection_qty_total"], 90)
        self.assertEqual(result["quality_metrics"]["input_qty_total"], 110)
        self.assertEqual(result["quality_metrics"]["defect_rate_pct"], 4.0)

    def test_good_qty_aliases(self):
        for column in ("양품수량", "OK수량"):
            with self.subTest(column=column):
                result = self._analyze([{column: 10, "생산수량": 12, "불량수량": 2}])
                self.assertEqual(result["column_mapping"]["good_qty"]["column"], column)
                self.assertEqual(result["quality_metrics"]["good_qty_total"], 10)

    def test_rate_override_as_defect_qty_is_rejected(self):
        rows = [
            {"생산수량": 1000, "불량수량": 25, "불량률": 2.5},
            {"생산수량": 1000, "불량수량": 25, "불량률": 2.5},
        ]
        result = self._analyze(rows, {"defect_qty": "불량률"})
        self.assertTrue(result["needs_clarification"])
        self.assertEqual(result["clarification"]["field"], "defect_qty")
        self.assertNotIn("불량률", result["clarification"]["candidates"])
        self.assertIn("불량수량", result["clarification"]["candidates"])
        self.assertIsNone(result["column_mapping"]["defect_qty"]["column"])
        metrics = result["quality_metrics"] or {}
        self.assertNotEqual(metrics.get("defect_qty_total"), 5.0)
        self.assertIsNone(metrics.get("defect_rate_pct"))
        self.assertTrue(any("비율" in w or "불량률" in w for w in result["warnings"]))

    def test_computed_rate_uses_qty_not_rate_column_sum(self):
        rows = [
            {"생산수량": 1000, "불량수량": 25, "불량률": 2.5},
        ]
        result = self._analyze(rows)
        self.assertEqual(result["quality_metrics"]["defect_qty_total"], 25)
        self.assertEqual(result["quality_metrics"]["defect_rate_pct"], 2.5)
        self.assertNotEqual(result["quality_metrics"]["defect_qty_total"], 2.5)

    def test_inspection_qty_is_not_used_as_production_denominator(self):
        rows = [{"검사수량": 100, "불량수량": 4}]
        result = self._analyze(rows)
        self.assertEqual(result["column_mapping"]["inspection_qty"]["column"], "검사수량")
        self.assertIsNone(result["column_mapping"]["production_qty"]["column"])
        self.assertIsNone((result["quality_metrics"] or {}).get("defect_rate_pct"))
        self.assertTrue(any("검사수량" in w and "가정하지" in w for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
