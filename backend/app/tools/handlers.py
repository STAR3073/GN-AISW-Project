"""Registry 어댑터. 구현 본문은 각 Tool 모듈을 그대로 호출한다."""

from __future__ import annotations

from typing import Any, Mapping

from app.agent.excel_reply import drop_stale_document_mapping
from app.tools.calendar_tool import create_calendar_events as run_create_calendar_events
from app.tools.document_generator import generate_draft as run_generate_draft
from app.tools.excel_analyzer import analyze_excel as run_analyze_excel
from app.tools.registry import ToolResult, register_tool
from app.tools.report_generator import generate_report as run_generate_report


@register_tool("create_tasks")
def handle_create_tasks(_args: dict[str, Any], state: Mapping[str, Any]) -> ToolResult:
    tasks = state.get("tasks") or []
    return ToolResult(ok=True, detail=f"Task {len(tasks)}건 준비")


@register_tool("create_calendar_events")
def handle_create_calendar_events(_args: dict[str, Any], state: Mapping[str, Any]) -> ToolResult:
    events = run_create_calendar_events(
        state.get("session_id") or "",
        list(state.get("actions") or []),
    )
    return ToolResult(
        ok=True,
        detail=f"Mock Calendar {len(events)}건 생성",
        data=events,
    )


@register_tool("generate_draft")
def handle_generate_draft(args: dict[str, Any], state: Mapping[str, Any]) -> ToolResult:
    title = args.get("title") or "업무 문서 초안"
    actions = list(state.get("actions") or [])
    draft = run_generate_draft(
        session_id=state.get("session_id") or "",
        title=title,
        document_summary=state.get("document_summary") or "",
        actions=actions,
        extra_context=state.get("user_message") or "",
    )
    want = title.replace("초안", "").strip()
    matched = False
    updated = []
    for action in actions:
        title_hit = want and (
            want in (action.get("title") or "") or (action.get("title") or "") in want
        )
        if title_hit and not matched:
            action = {**action, "status": "DRAFT_COMPLETED"}
            matched = True
        updated.append(action)
    return ToolResult(
        ok=True,
        detail=f"초안 저장: {draft['path']}",
        data=draft,
        state_updates={"actions": updated},
    )


@register_tool("analyze_excel")
def handle_analyze_excel(args: dict[str, Any], state: Mapping[str, Any]) -> ToolResult:
    payload = run_analyze_excel(args, state)
    updates = None
    stale = payload.get("stale_overrides") if isinstance(payload, dict) else None
    if stale:
        file_info = payload.get("file") if isinstance(payload.get("file"), dict) else {}
        document_id = str((file_info or {}).get("id") or args.get("document_id") or "")
        updates = {
            "document_column_mappings": drop_stale_document_mapping(state, document_id, list(stale)),
        }
    return ToolResult(
        ok=bool(payload.get("ok")),
        detail=str(payload.get("detail") or "Excel 분석"),
        data=payload,
        state_updates=updates,
    )


@register_tool("generate_report")
def handle_generate_report(args: dict[str, Any], state: Mapping[str, Any]) -> ToolResult:
    payload = run_generate_report(args, state)
    return ToolResult(
        ok=bool(payload.get("ok")),
        detail=str(payload.get("detail") or "품질 보고서 생성"),
        data=payload,
    )
