from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent.graph import build_graph
from app.agent.nodes.tool_executor import tool_executor
from app.tools.registry import (
    UnknownToolError,
    invoke_tool,
    is_registered,
    registered_tool_names,
)


class ToolRegistryTests(unittest.TestCase):
    def test_current_builtin_tools_are_registered(self):
        names = set(registered_tool_names())
        self.assertEqual(
            names,
            {"create_tasks", "create_calendar_events", "generate_draft", "analyze_excel", "generate_report"},
        )

    def test_future_tools_are_not_registered(self):
        for name in (
            "search_documents",
            "compare_documents",
            "extract_data",
            "convert_document",
        ):
            self.assertFalse(is_registered(name))

    def test_unknown_tool_raises_and_does_not_run(self):
        with self.assertRaises(UnknownToolError) as ctx:
            invoke_tool("os.system", {"cmd": "echo hi"}, {})
        self.assertIn("os.system", str(ctx.exception))
        with self.assertRaises(UnknownToolError):
            invoke_tool("__import__", {}, {})
        with self.assertRaises(UnknownToolError):
            invoke_tool("search_documents", {}, {})
        with self.assertRaises(UnknownToolError):
            invoke_tool("", {}, {})
        with self.assertRaises(UnknownToolError):
            invoke_tool(None, {}, {})

    def test_create_tasks_result_matches_previous_behavior(self):
        result = invoke_tool(
            "create_tasks",
            {},
            {"tasks": [{"title": "a"}, {"title": "b"}]},
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "Task 2건 준비")
        self.assertIsNone(result.data)
        self.assertIsNone(result.state_updates)

    def test_create_calendar_events_delegates_to_existing_impl(self):
        events = [{"id": "e1", "title": "[마감] 제출"}]
        with patch("app.tools.handlers.run_create_calendar_events", return_value=events) as mocked:
            result = invoke_tool(
                "create_calendar_events",
                {},
                {
                    "session_id": "sess-1",
                    "actions": [{"title": "제출", "deadline": "2026-10-10"}],
                },
            )
        mocked.assert_called_once_with(
            "sess-1",
            [{"title": "제출", "deadline": "2026-10-10"}],
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "Mock Calendar 1건 생성")
        self.assertEqual(result.data, events)

    def test_generate_draft_delegates_and_marks_matching_action(self):
        draft = {"title": "완료보고서 초안", "path": "D:/tmp/draft.md", "preview": "본문"}
        with patch("app.tools.handlers.run_generate_draft", return_value=draft) as mocked:
            result = invoke_tool(
                "generate_draft",
                {"title": "완료보고서 초안"},
                {
                    "session_id": "sess-1",
                    "document_summary": "요약",
                    "user_message": "초안 작성해",
                    "actions": [
                        {"title": "완료보고서", "status": "TODO"},
                        {"title": "설명서", "status": "TODO"},
                    ],
                },
            )
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["session_id"], "sess-1")
        self.assertEqual(kwargs["title"], "완료보고서 초안")
        self.assertEqual(kwargs["document_summary"], "요약")
        self.assertEqual(kwargs["extra_context"], "초안 작성해")
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "초안 저장: D:/tmp/draft.md")
        self.assertEqual(result.data, draft)
        updated = result.state_updates["actions"]
        self.assertEqual(updated[0]["status"], "DRAFT_COMPLETED")
        self.assertEqual(updated[1]["status"], "TODO")


class ToolExecutorDispatchTests(unittest.TestCase):
    def test_executor_keeps_step_name_and_dispatches_create_tasks(self):
        out = tool_executor(
            {
                "session_id": "s1",
                "tasks": [{"title": "t1"}],
                "actions": [{"title": "a1"}],
                "tool_calls": [{"tool": "create_tasks", "args": {}}],
                "execution_results": [],
            }
        )
        self.assertEqual(out["current_step"], "tool_executor")
        self.assertEqual(out["actions"], [{"title": "a1"}])
        self.assertEqual(
            out["execution_results"],
            [{"tool": "create_tasks", "ok": True, "detail": "Task 1건 준비"}],
        )

    def test_executor_unknown_tool_fails_safely(self):
        out = tool_executor(
            {
                "actions": [],
                "tool_calls": [{"tool": "search_documents", "args": {"query": "x"}}],
                "execution_results": [],
            }
        )
        self.assertEqual(out["current_step"], "tool_executor")
        self.assertEqual(len(out["execution_results"]), 1)
        row = out["execution_results"][0]
        self.assertEqual(row["tool"], "search_documents")
        self.assertFalse(row["ok"])
        self.assertIn("search_documents", row["detail"])
        self.assertNotIn("data", row)

    def test_executor_runs_multiple_registered_tools_in_order(self):
        events = [{"id": "e1"}]
        with patch("app.tools.handlers.run_create_calendar_events", return_value=events):
            out = tool_executor(
                {
                    "session_id": "s1",
                    "tasks": [{"title": "t1"}, {"title": "t2"}],
                    "actions": [{"title": "제출", "deadline": "2026-10-10"}],
                    "tool_calls": [
                        {"tool": "create_tasks", "args": {}},
                        {"tool": "create_calendar_events", "args": {}},
                    ],
                    "execution_results": [],
                }
            )
        self.assertEqual(out["current_step"], "tool_executor")
        self.assertEqual(out["execution_results"][0]["tool"], "create_tasks")
        self.assertEqual(out["execution_results"][0]["detail"], "Task 2건 준비")
        self.assertEqual(out["execution_results"][1]["tool"], "create_calendar_events")
        self.assertEqual(out["execution_results"][1]["detail"], "Mock Calendar 1건 생성")
        self.assertEqual(out["execution_results"][1]["data"], events)


class GraphUnchangedTests(unittest.TestCase):
    def test_graph_still_has_tool_selector_executor_verifier(self):
        compiled = build_graph()
        nodes = set(compiled.nodes)
        for name in (
            "intent_router",
            "document_parser",
            "document_understanding",
            "action_extractor",
            "task_planner",
            "planner",
            "tool_selector",
            "tool_executor",
            "verifier",
            "state_updater",
            "responder",
        ):
            self.assertIn(name, nodes)


if __name__ == "__main__":
    unittest.main()
