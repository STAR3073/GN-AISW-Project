"""사용자 목표를 실행 계획으로 만든다. ingest의 task_planner와 역할을 분리한다."""

from __future__ import annotations

from typing import Any, Mapping

from app.agent.excel_reply import (
    mapping_for_document,
    merge_resolved_mapping,
    persistent_mapping_for_document,
    remember_document_mapping,
)
from app.agent.state import AgentState
from app.agent.evaluator import analysis_filter_from_state
from app.agent.work_intent import (
    match_clarification_choice,
    tabular_documents,
    wants_data_analysis,
    wants_quality_report,
)
from app.schemas.agent import PlanStep, WorkPlan
from app.tools.registry import is_registered


def build_work_plan(state: Mapping[str, Any]) -> WorkPlan:
    message = (state.get("user_message") or "").strip()
    pending = state.get("pending_clarification") if isinstance(state.get("pending_clarification"), dict) else None
    files = tabular_documents(state)
    goal = (state.get("interpreted_goal") or "").strip() or message
    capability = str(state.get("suggested_capability") or "")
    source = state.get("intent_source") or ""

    if pending:
        if pending.get("type") == "upload" and files:
            pending = None
        else:
            resumed = _resume_clarification(message, pending, files, state)
            if resumed:
                return resumed
            return WorkPlan(
                goal=goal or "이전 확인 사항을 이어서 처리",
                requires_tools=False,
                needs_clarification=True,
                clarification=_pending_prompt(pending),
                pending=dict(pending),
                route="reply",
            )

    has_tabular = bool(files)
    row_filter = analysis_filter_from_state(state)
    tabular_job = (
        capability == "tabular_data_analysis"
        or wants_quality_report(message, has_tabular)
        or wants_data_analysis(message, has_tabular)
        or _mentions_spreadsheet(message)
        or bool(row_filter)
        or (
            state.get("intent") == "execute"
            and source == "semantic"
            and has_tabular
            and capability not in {"draft", "document_ingest"}
        )
    )
    if tabular_job:
        plan = _plan_excel_analysis(
            goal or message,
            files,
            include_report=_wants_report_plan(state, goal or message),
            state=state,
        )
        if goal:
            plan.goal = goal
        retry_args = state.get("evaluation_retry_args") if state.get("evaluation_status") == "retry" else None
        if retry_args:
            for step in plan.steps:
                if step.tool == "analyze_excel":
                    step.args = {**(step.args or {}), **dict(retry_args)}
        return plan

    if capability == "draft" or any(k in message for k in ("초안", "드래프트", "작성해")):
        title = "업무 문서 초안"
        return WorkPlan(
            goal="문서 초안 작성",
            requires_tools=True,
            steps=[
                PlanStep(id=1, description="문서 초안 생성", tool="generate_draft", args={"title": title})
            ],
            route="tools",
        )

    return WorkPlan(
        goal=message[:80] or "사용자 요청 처리",
        requires_tools=False,
        route="reply",
    )


def planner(state: AgentState) -> dict:
    original_pending = state.get("pending_clarification") if isinstance(state.get("pending_clarification"), dict) else None
    plan = build_work_plan(state)
    if plan.needs_clarification:
        pending = plan.pending or original_pending or _pending_from_plan(plan)
    else:
        pending = None
    out = {
        "plan": plan.model_dump(),
        "plan_steps": [step.model_dump() for step in plan.steps],
        "pending_clarification": pending,
        "current_step": "planner",
        "analysis_filter": _filter_from_plan(plan),
    }
    if original_pending and original_pending.get("type") == "column":
        for step in plan.steps:
            if step.tool != "analyze_excel":
                continue
            mapping = (step.args or {}).get("column_mapping") if isinstance(step.args, dict) else None
            doc_id = str(step.document_id or (step.args or {}).get("document_id") or "")
            if mapping and doc_id:
                out["document_column_mappings"] = remember_document_mapping(state, doc_id, mapping)
            break
    return out


def _mentions_spreadsheet(message: str) -> bool:
    lowered = message.lower()
    return any(k in lowered for k in ("xlsx", "xls", "csv", "엑셀", "excel"))


def _plan_excel_analysis(
    message: str,
    files: list[dict[str, Any]],
    *,
    include_report: bool = False,
    state: Mapping[str, Any] | None = None,
) -> WorkPlan:
    if not files:
        return WorkPlan(
            goal="품질 데이터 분석" if not include_report else "이번 주 품질 현황 보고서 작성",
            requires_tools=False,
            needs_clarification=True,
            clarification=(
                "분석할 Excel/CSV 파일이 없습니다. "
                "프로젝트 또는 현재 대화에 xlsx, xls, csv 파일을 업로드하거나 참조해 주세요."
            ),
            pending={
                "type": "upload",
                "tool": "analyze_excel",
                "candidates": [],
                "message": "파일을 업로드해 주세요.",
                "include_report": include_report,
            },
            route="reply",
        )
    if len(files) > 1:
        names = [row.get("filename") or row.get("id") for row in files]
        numbered = "\n".join(f"{idx}. {name}" for idx, name in enumerate(names, 1))
        return WorkPlan(
            goal="품질 데이터 분석" if not include_report else "이번 주 품질 현황 보고서 작성",
            requires_tools=False,
            needs_clarification=True,
            clarification=f"분석할 Excel/CSV 파일이 여러 개입니다. 어떤 파일을 분석할까요?\n{numbered}",
            pending={
                "type": "file",
                "tool": "analyze_excel",
                "candidates": [
                    {"id": row.get("id"), "filename": row.get("filename")} for row in files
                ],
                "message": "분석할 파일을 선택해 주세요.",
                "include_report": include_report,
            },
            route="reply",
        )
    chosen = files[0]
    document_id = str(chosen.get("id") or "")
    args = {"document_id": document_id}
    persistent = persistent_mapping_for_document(state, document_id)
    if persistent:
        args["column_mapping"] = persistent
    if not include_report:
        _apply_row_filter(args, state)
    steps = [
        PlanStep(
            id=1,
            description="품질 Excel/CSV 데이터 분석",
            tool="analyze_excel",
            document_id=document_id,
            args=args,
        )
    ]
    if include_report and is_registered("generate_report"):
        steps.append(
            PlanStep(
                id=2,
                description="검증된 분석 결과로 품질 보고서 생성",
                tool="generate_report",
                document_id=str(chosen.get("id") or ""),
                args={"depends_on": "analyze_excel"},
            )
        )
    return WorkPlan(
        goal="이번 주 품질 현황 보고서 작성" if include_report else "업로드된 품질 데이터의 불량 현황 분석",
        requires_tools=True,
        steps=steps,
        route="tools",
    )


def _resume_clarification(
    message: str, pending: dict[str, Any], files: list[dict[str, Any]], state: Mapping[str, Any]
) -> WorkPlan | None:
    kind = pending.get("type")
    if kind == "upload":
        if not files:
            return None
        return _plan_excel_analysis(
            (state.get("interpreted_goal") or message or "").strip() or message,
            files,
            include_report=_wants_report_plan(state, message),
            state=state,
        )
    if kind == "file":
        picked = match_clarification_choice(message, list(pending.get("candidates") or []))
        chosen = None
        if isinstance(picked, dict) and picked.get("id"):
            chosen = picked
        elif isinstance(picked, str):
            chosen = next((row for row in files if (row.get("filename") or "") == picked or row.get("id") == picked), None)
        if chosen:
            document_id = str(chosen.get("id") or "")
            args = {"document_id": document_id}
            persistent = persistent_mapping_for_document(state, document_id)
            if persistent:
                args["column_mapping"] = persistent
            if not _wants_report_plan(state, message):
                _apply_row_filter(args, state)
            plan = WorkPlan(
                goal="선택한 파일 분석",
                requires_tools=True,
                steps=[
                    PlanStep(
                        id=1,
                        description="선택한 Excel/CSV 분석",
                        tool="analyze_excel",
                        document_id=document_id,
                        args=args,
                    )
                ],
                route="tools",
            )
            return _maybe_append_report_step(plan, state)
        return None
    if kind == "column":
        picked = match_clarification_choice(message, list(pending.get("candidates") or []))
        if not picked:
            return None
        column = str(picked.get("filename") if isinstance(picked, dict) else picked)
        field = str(pending.get("field") or "")
        document_id = str(pending.get("document_id") or (files[0].get("id") if files else "") or "")
        if not is_registered("analyze_excel"):
            return None
        previous = mapping_for_document(
            pending.get("resolved_mapping"),
            document_id,
            str(pending.get("document_id") or "") or None,
        )
        base = {**persistent_mapping_for_document(state, document_id), **previous}
        mapping = merge_resolved_mapping(base, field, column)
        args = {"document_id": document_id, "column_mapping": mapping}
        if not _wants_report_plan(state, message):
            _apply_row_filter(args, state)
        plan = WorkPlan(
            goal="확인된 컬럼으로 품질 데이터 재분석",
            requires_tools=True,
            steps=[
                PlanStep(
                    id=1,
                    description="컬럼 매핑을 반영한 Excel 분석",
                    tool="analyze_excel",
                    document_id=document_id,
                    args=args,
                )
            ],
            route="tools",
        )
        return _maybe_append_report_step(plan, state)
    return None


def _wants_report_plan(state: Mapping[str, Any], text: str) -> bool:
    pending = state.get("pending_clarification") if isinstance(state.get("pending_clarification"), dict) else {}
    if pending.get("include_report"):
        return True
    return wants_quality_report(text, bool(tabular_documents(state)))


def _apply_row_filter(args: dict[str, Any], state: Mapping[str, Any] | None) -> None:
    filt = analysis_filter_from_state(state or {})
    if filt:
        args["filter"] = filt


def _filter_from_plan(plan: WorkPlan) -> dict[str, str]:
    for step in plan.steps:
        if step.tool != "analyze_excel":
            continue
        filt = (step.args or {}).get("filter") if isinstance(step.args, dict) else None
        if isinstance(filt, dict) and filt.get("role") and filt.get("value"):
            return {"role": str(filt["role"]), "value": str(filt["value"])}
    return {}


def _maybe_append_report_step(plan: WorkPlan, state: Mapping[str, Any]) -> WorkPlan:
    if not _wants_report_plan(state, (state.get("interpreted_goal") or state.get("user_message") or "")):
        return plan
    if any(step.tool == "generate_report" for step in plan.steps):
        return plan
    if not is_registered("generate_report"):
        return plan
    doc_id = plan.steps[0].document_id if plan.steps else ""
    plan.goal = plan.goal or "이번 주 품질 현황 보고서 작성"
    plan.steps.append(
        PlanStep(
            id=(plan.steps[-1].id if plan.steps else 0) + 1,
            description="검증된 분석 결과로 품질 보고서 생성",
            tool="generate_report",
            document_id=doc_id,
            args={"depends_on": "analyze_excel"},
        )
    )
    return plan


def _pending_prompt(pending: dict[str, Any]) -> str:
    if pending.get("type") == "file":
        candidates = pending.get("candidates") or []
        lines = [pending.get("message") or "분석할 파일을 선택해 주세요."]
        for idx, item in enumerate(candidates, 1):
            name = item.get("filename") if isinstance(item, dict) else item
            lines.append(f"{idx}. {name}")
        return "\n".join(lines)
    if pending.get("type") == "column":
        candidates = pending.get("candidates") or []
        lines = [pending.get("message") or "컬럼을 선택해 주세요.", ""]
        lines.append("다음 중 어떤 컬럼이 실제 값인가요?")
        for idx, name in enumerate(candidates, 1):
            lines.append(f"{idx}. {name}")
        return "\n".join(lines)
    return pending.get("message") or "추가 정보가 필요합니다."


def _pending_from_plan(plan: WorkPlan) -> dict[str, Any] | None:
    if plan.pending:
        return plan.pending
    if plan.needs_clarification:
        return {"type": "upload", "tool": "analyze_excel", "message": plan.clarification, "candidates": []}
    return None
