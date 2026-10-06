"""Goal vs plan vs tool results. Rule-first Evaluator. Tool은 실행하지 않는다."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from app.agent.work_intent import wants_quality_report

from app.schemas.agent import EvaluationCheck, EvaluationResult

MAX_RETRY = 2
NON_RETRYABLE_ERRORS = {
    "file_not_found",
    "unsupported_type",
    "empty_file",
    "empty_sheet",
    "file_too_large",
}

_PERIOD_HINTS = ("지난주", "지난번", "이전", "비교", "달라", "안 좋아", "좋아졌", "변화", "추이")
_RATE_HINTS = ("불량률", "불량 현황", "품질 현황", "품질")
_ITEM_HINTS = ("품목별", "품목 기준", "품목")
_EQUIP_HINTS = ("설비", "호기", "장비")
_PROCESS_HINTS = ("공정별", "공정")
_ITEM_NAME = re.compile(r"(?:^|[\s,])([A-Za-z0-9가-힣]{1,12}) ?품목")
_EQUIP_NAME = re.compile(r"(\d+)\s*호기")
_PROCESS_NAME = re.compile(r"([A-Za-z0-9가-힣]{2,12})\s*공정")
_PROCESS_SKIP = {"별", "각", "해당", "이번", "전체", "주요", "관련", "해당"}
_FOLLOWUP_HINTS = (
    "지난주",
    "지난번",
    "이전",
    "비교",
    "달라",
    "안 좋아",
    "좋아졌",
    "변화",
    "추이",
    "왜",
    "원인",
    "그건",
    "그럼",
    "그 설비",
    "그 품목",
    "그 공정",
    "상태",
)
_CLEAR_FILTER_HINTS = ("품목별", "공정별", "설비별", "전체 현황", "전체 품질", "이 엑셀", "파일 전체")
FILTER_ROLES = ("equipment", "item", "process")


def evaluate(state: Mapping[str, Any]) -> EvaluationResult:
    intent = state.get("intent") or ""
    if intent == "query":
        result = EvaluationResult(status="pass", goal_satisfied=True)
    elif intent == "ingest":
        result = _evaluate_ingest(state)
    else:
        result = _evaluate_execute(state)
    return maybe_llm_evaluate(state, result)


def maybe_llm_evaluate(state: Mapping[str, Any], rule_result: EvaluationResult) -> EvaluationResult:
    """규칙만으로 목표 충족을 판단하기 어려울 때의 확장 지점. v1은 호출하지 않는다."""
    return rule_result


def _evaluate_ingest(state: Mapping[str, Any]) -> EvaluationResult:
    checks: list[EvaluationCheck] = []
    missing: list[str] = []
    actions = list(state.get("actions") or [])
    tasks = list(state.get("tasks") or [])
    results = list(state.get("execution_results") or [])

    has_actions = bool(actions)
    checks.append(EvaluationCheck(name="actions_extracted", passed=has_actions))
    if not has_actions:
        missing.append("actions")

    has_tasks = bool(tasks)
    checks.append(EvaluationCheck(name="tasks_planned", passed=has_tasks, detail="Task가 없습니다." if not has_tasks else ""))
    if not has_tasks:
        missing.append("tasks")

    calendar_ok = any(r.get("tool") == "create_calendar_events" and r.get("ok") for r in results)
    checks.append(EvaluationCheck(name="calendar_executed", passed=calendar_ok))
    if not calendar_ok:
        missing.append("calendar")

    if missing:
        retryable = _retry_left(state)
        status = "retry" if retryable else "fail"
        return EvaluationResult(
            status=status,
            goal_satisfied=False,
            checks=checks,
            missing_requirements=missing,
            retry_instruction="누락된 Task/Calendar Tool을 다시 실행" if status == "retry" else "",
            retry_target="tools" if status == "retry" else "none",
            user_message=None if status == "retry" else "업무 추출 결과를 충분히 확인하지 못했습니다. 문서를 다시 지정해 주세요.",
        )
    notes = [a.get("title") for a in actions if not a.get("deadline")]
    if notes:
        checks.append(EvaluationCheck(name="deadlines", passed=True, detail=f"마감일 없는 Action: {', '.join(notes[:4])}"))
    return EvaluationResult(status="pass", goal_satisfied=True, checks=checks, validated_facts={"action_count": len(actions), "task_count": len(tasks)})


def _evaluate_execute(state: Mapping[str, Any]) -> EvaluationResult:
    results = list(state.get("execution_results") or [])
    calls = list(state.get("tool_calls") or [])
    excel = _latest_excel(results)
    report = _latest_tool(results, "generate_report")
    used_excel = bool(excel) or any(c.get("tool") == "analyze_excel" for c in calls)
    scope = infer_analysis_scope(state)
    wants_report = _plan_wants_report(state)

    if report is not None:
        return _evaluate_report(state, report)

    if used_excel or scope.needs_excel or wants_report:
        if wants_report:
            ready = _evaluate_excel_for_report(state, excel)
            if ready.status != "pass":
                return ready
            if _report_step_pending(state):
                return EvaluationResult(
                    status="continue",
                    goal_satisfied=False,
                    checks=ready.checks + [EvaluationCheck(name="report_pending", passed=True, detail="분석 검증 후 보고서를 생성합니다.")],
                    validated_facts=ready.validated_facts,
                    retry_target="tools",
                )
            if _retry_left(state):
                return EvaluationResult(
                    status="retry",
                    goal_satisfied=False,
                    checks=ready.checks + [EvaluationCheck(name="report_planned", passed=False, detail="보고서 생성 단계가 계획에 없습니다.")],
                    missing_requirements=["generate_report"],
                    retry_instruction="검증된 분석 결과로 generate_report 단계를 계획에 추가",
                    retry_target="planner",
                    validated_facts=ready.validated_facts,
                )
            return EvaluationResult(
                status="fail",
                checks=ready.checks,
                missing_requirements=["generate_report"],
                user_message="분석은 끝났지만 보고서를 생성하지 못했습니다.",
                validated_facts=ready.validated_facts,
            )
        return _evaluate_excel(state, excel, scope)

    if not results:
        if _retry_left(state):
            plan_steps = (state.get("plan") or {}).get("steps") or state.get("plan_steps") or []
            target = "planner" if not any(s.get("tool") for s in plan_steps) else "tools"
            return EvaluationResult(
                status="retry",
                checks=[EvaluationCheck(name="tool_executed", passed=False, detail="Tool 실행 결과가 없습니다.")],
                missing_requirements=["tool_result"],
                retry_instruction="계획된 Tool을 다시 실행",
                retry_target=target,
            )
        return EvaluationResult(
            status="fail",
            checks=[EvaluationCheck(name="tool_executed", passed=False)],
            missing_requirements=["tool_result"],
            user_message="현재 데이터만으로 요청하신 분석을 충분히 완료하기 어렵습니다.",
        )

    latest = results[-1]
    if not latest.get("ok"):
        return _tool_failure(state, latest)

    facts = {"tool": latest.get("tool"), "ok": True, "detail": latest.get("detail") or ""}
    return EvaluationResult(status="pass", goal_satisfied=True, checks=[EvaluationCheck(name="tool_ok", passed=True)], validated_facts=facts)


def _evaluate_excel(state: Mapping[str, Any], excel: dict[str, Any] | None, scope: "AnalysisScope") -> EvaluationResult:
    checks: list[EvaluationCheck] = []
    missing: list[str] = []
    retry_args: dict[str, Any] = {}
    retry_instruction = ""
    retry_target = "tools"

    if not excel:
        if _retry_left(state) and not any(c.get("tool") == "analyze_excel" for c in (state.get("tool_calls") or [])):
            return EvaluationResult(
                status="retry",
                checks=[EvaluationCheck(name="analyze_excel_executed", passed=False)],
                missing_requirements=["analyze_excel"],
                retry_instruction="Excel/CSV 분석 Tool을 계획에 포함하고 실행",
                retry_target="planner",
            )
        if _retry_left(state):
            return EvaluationResult(
                status="retry",
                checks=[EvaluationCheck(name="analyze_excel_executed", passed=False)],
                missing_requirements=["analyze_excel"],
                retry_instruction="analyze_excel을 다시 실행",
                retry_target="tools",
            )
        return EvaluationResult(
            status="fail",
            checks=[EvaluationCheck(name="analyze_excel_executed", passed=False)],
            missing_requirements=["analyze_excel"],
            user_message="현재 데이터만으로 요청하신 분석을 충분히 완료하기 어렵습니다.",
        )

    if excel.get("ok") is False:
        return _tool_failure(state, {"tool": "analyze_excel", "ok": False, "data": excel, "detail": excel.get("detail")})

    if excel.get("needs_clarification"):
        info = excel.get("clarification") or {}
        return EvaluationResult(
            status="clarify",
            checks=[EvaluationCheck(name="needs_clarification", passed=False, detail=info.get("message") or "")],
            missing_requirements=[info.get("field") or "column_mapping"],
            user_message=None,
        )

    mapping = excel.get("column_mapping") or {}
    scoped = _scoped_excel(excel)
    quality = scoped.get("quality_metrics") or excel.get("quality_metrics") or {}
    groups = scoped.get("group_analysis") or {}

    checks.append(EvaluationCheck(name="tool_ok", passed=True))

    if scope.wants_defect_rate or scope.wants_item_group:
        prod_col = _mapped(mapping, "production_qty")
        defect_col = _mapped(mapping, "defect_qty")
        defect_ok = bool(defect_col or _mapped(mapping, "result"))
        checks.append(EvaluationCheck(name="production_qty_column", passed=bool(prod_col)))
        checks.append(EvaluationCheck(name="defect_basis", passed=defect_ok))
        if not prod_col:
            missing.append("production_qty")
        if not defect_ok:
            missing.append("defect_basis")

        rate = quality.get("defect_rate_pct") if isinstance(quality, dict) else None
        prod_total = quality.get("production_qty_total") if isinstance(quality, dict) else None
        defect_total = quality.get("defect_qty_total") if isinstance(quality, dict) else None
        formula = (quality.get("formula") if isinstance(quality, dict) else None) or ""
        rate_ok = (
            rate is not None
            and bool(prod_col)
            and bool(defect_col)
            and prod_total not in (None, 0)
            and defect_total is not None
            and "불량수량 / 생산수량" in str(formula)
        )
        checks.append(EvaluationCheck(name="defect_rate", passed=bool(rate_ok)))
        checks.append(EvaluationCheck(name="nonzero_denominator", passed=prod_total not in (None, 0)))
        if not rate_ok:
            missing.append("defect_rate")
        if prod_total == 0:
            missing.append("nonzero_denominator")

    if scope.wants_item_group:
        items = list(groups.get("by_item") or [])
        has_group = bool(items)
        checks.append(EvaluationCheck(name="item_group", passed=has_group))
        if not has_group:
            missing.append("item_group")
            if not _mapped(mapping, "item"):
                missing.append("item_column")
        elif scope.item_name:
            matched = _match_group(items, scope.item_name)
            checks.append(EvaluationCheck(name="item_filter", passed=bool(matched), detail=scope.item_name))
            if not matched:
                missing.append("item_filter")
                retry_args, retry_instruction = _maybe_expand_top_n(state, retry_args, retry_instruction)

    if scope.wants_period:
        date_col = _mapped(mapping, "date")
        checks.append(EvaluationCheck(name="date_column", passed=bool(date_col)))
        if not date_col:
            missing.append("date_column")
        period_src = _scoped_excel(excel)
        change = period_src.get("change_detection") if isinstance(period_src.get("change_detection"), dict) else {}
        period = period_src.get("period_analysis") or {}
        prev = change.get("previous_period") if isinstance(change, dict) else None
        latest_p = change.get("latest_period") if isinstance(change, dict) else None
        metric = False
        if isinstance(change, dict):
            metric = any(change.get(k) is not None for k in ("defect_rate_delta_pp", "defect_qty_delta", "rows_delta"))
        if not (prev and latest_p):
            weekly = list((period or {}).get("weekly") or [])
            monthly = list((period or {}).get("monthly") or [])
            series = monthly if len(monthly) >= 2 else weekly
            if len(series) >= 2:
                prev = series[-2].get("period")
                latest_p = series[-1].get("period")
                metric = any(series[-1].get(k) is not None for k in ("defect_rate_pct", "defect_qty", "rows"))
        period_ok = bool(date_col and prev and latest_p and metric)
        checks.append(EvaluationCheck(name="period_comparison", passed=period_ok))
        if not period_ok:
            missing.append("previous_period_comparison")

    if scope.wants_equipment:
        ctx = excel.get("filter_context") if isinstance(excel.get("filter_context"), dict) else {}
        if ctx.get("role") == "equipment":
            matched = bool(ctx.get("matched"))
            checks.append(EvaluationCheck(name="equipment_column", passed=ctx.get("error") != "missing_column"))
            if ctx.get("error") == "missing_column":
                missing.append("equipment_column")
            checks.append(EvaluationCheck(name="equipment_filter", passed=matched, detail=str(ctx.get("value") or "")))
            if not matched and "equipment_column" not in missing:
                missing.append("equipment_filter")
        else:
            equip_col = _mapped(mapping, "equipment")
            checks.append(EvaluationCheck(name="equipment_column", passed=bool(equip_col)))
            if not equip_col:
                missing.append("equipment_column")
            rows = list(groups.get("by_equipment") or [])
            name = scope.equipment_name or ""
            matched = _match_group(rows, name) if name else (rows[0] if rows else None)
            checks.append(EvaluationCheck(name="equipment_filter", passed=bool(matched), detail=name))
            if not matched:
                missing.append("equipment_filter")
                if equip_col:
                    retry_args, retry_instruction = _maybe_expand_top_n(state, retry_args, retry_instruction)

    if missing:
        return _unmet_excel(state, checks, missing, scope, retry_args, retry_instruction, retry_target)

    facts = build_validated_facts(excel, scope)
    return EvaluationResult(
        status="pass",
        goal_satisfied=True,
        checks=checks,
        validated_facts=facts,
    )


def _unmet_excel(
    state: Mapping[str, Any],
    checks: list[EvaluationCheck],
    missing: list[str],
    scope: "AnalysisScope",
    retry_args: dict[str, Any],
    retry_instruction: str,
    retry_target: str,
) -> EvaluationResult:
    structural = {"production_qty", "defect_basis", "item_column", "date_column", "equipment_column", "nonzero_denominator"}
    if set(missing) & structural:
        return EvaluationResult(
            status="clarify",
            checks=checks,
            missing_requirements=missing,
            user_message=_clarify_message(missing, scope),
            validated_facts=build_validated_facts(_latest_excel(state.get("execution_results") or []) or {}, scope, allow_partial=True),
        )
    if retry_args and _retry_left(state):
        return EvaluationResult(
            status="retry",
            checks=checks,
            missing_requirements=missing,
            retry_instruction=retry_instruction or "분석 범위를 넓혀 다시 실행",
            retry_target=retry_target,
            retry_args=retry_args,
            user_message=None,
        )
    if _retry_left(state) and "analyze_excel" in missing:
        return EvaluationResult(
            status="retry",
            checks=checks,
            missing_requirements=missing,
            retry_instruction=retry_instruction or "analyze_excel을 다시 실행",
            retry_target="tools",
        )
    detail = _clarify_message(missing, scope)
    if int(state.get("retry_count") or 0) >= MAX_RETRY:
        prefix = "현재 데이터만으로 요청하신 분석을 충분히 완료하기 어렵습니다."
        if detail and detail not in prefix:
            detail = f"{prefix} {detail}"
        else:
            detail = prefix
        return EvaluationResult(
            status="fail",
            checks=checks,
            missing_requirements=missing,
            user_message=detail,
            validated_facts=build_validated_facts(_latest_excel(state.get("execution_results") or []) or {}, scope, allow_partial=True),
        )
    return EvaluationResult(
        status="clarify",
        checks=checks,
        missing_requirements=missing,
        user_message=detail or "현재 데이터만으로 요청하신 분석을 충분히 완료하기 어렵습니다.",
        validated_facts=build_validated_facts(_latest_excel(state.get("execution_results") or []) or {}, scope, allow_partial=True),
    )


def _tool_failure(state: Mapping[str, Any], row: Mapping[str, Any]) -> EvaluationResult:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    error = str(data.get("error") or "")
    detail = str(row.get("detail") or data.get("detail") or "Tool 실행에 실패했습니다.")
    check = EvaluationCheck(name="tool_ok", passed=False, detail=detail)
    if error in NON_RETRYABLE_ERRORS or not _retry_left(state):
        return EvaluationResult(
            status="fail",
            checks=[check],
            missing_requirements=["tool_ok"],
            user_message=detail or "현재 데이터만으로 요청하신 분석을 충분히 완료하기 어렵습니다.",
        )
    return EvaluationResult(
        status="retry",
        checks=[check],
        missing_requirements=["tool_ok"],
        retry_instruction="실패한 Tool을 동일 계획으로 다시 실행",
        retry_target="tools",
    )


def infer_analysis_scope(state: Mapping[str, Any]) -> "AnalysisScope":
    text = _goal_text(state)
    extracted = _filter_from_text(text)
    reused = None if extracted else _reused_analysis_filter(state, text)
    chosen = extracted or reused
    equip = chosen["value"] if chosen and chosen.get("role") == "equipment" else None
    item = chosen["value"] if chosen and chosen.get("role") == "item" else None
    process = chosen["value"] if chosen and chosen.get("role") == "process" else None
    if not item:
        found_item = _ITEM_NAME.search(text)
        if found_item and found_item.group(1) not in {"별", "각", "해당"}:
            item = found_item.group(1)
    wants_period = any(h in text for h in _PERIOD_HINTS)
    wants_item = any(h in text for h in _ITEM_HINTS) or bool(item)
    wants_equip = bool(equip) or (any(h in text for h in _EQUIP_HINTS) and not process)
    wants_process = bool(process) or any(h in text for h in ("공정별",))
    wants_rate = any(h in text for h in _RATE_HINTS) or wants_item
    needs_excel = wants_period or wants_item or wants_equip or wants_process or wants_rate or "분석" in text or "상태" in text
    return AnalysisScope(
        wants_defect_rate=wants_rate,
        wants_item_group=wants_item,
        wants_period=wants_period,
        wants_equipment=wants_equip,
        equipment_name=equip,
        item_name=item,
        process_name=process,
        needs_excel=needs_excel,
    )


def analysis_filter_from_state(state: Mapping[str, Any]) -> dict[str, str] | None:
    if wants_quality_report(_goal_text(state), bool(state.get("documents"))):
        return None
    scope = infer_analysis_scope(state)
    if scope.equipment_name:
        return {"role": "equipment", "value": str(scope.equipment_name)}
    if scope.item_name and not any(h in _goal_text(state) for h in ("품목별",)):
        return {"role": "item", "value": str(scope.item_name)}
    if scope.process_name:
        return {"role": "process", "value": str(scope.process_name)}
    return None


def _filter_from_text(text: str) -> dict[str, str] | None:
    found = _EQUIP_NAME.search(text or "")
    if found:
        return {"role": "equipment", "value": f"{found.group(1)}호기"}
    found_item = _ITEM_NAME.search(text or "")
    if found_item and found_item.group(1) not in {"별", "각", "해당"}:
        return {"role": "item", "value": found_item.group(1)}
    found_proc = _PROCESS_NAME.search(text or "")
    if found_proc and found_proc.group(1) not in _PROCESS_SKIP:
        return {"role": "process", "value": found_proc.group(1)}
    return None


def _reused_analysis_filter(state: Mapping[str, Any], text: str) -> dict[str, str] | None:
    if wants_quality_report(text, False):
        return None
    if any(h in (text or "") for h in _CLEAR_FILTER_HINTS):
        return None
    followup = any(h in (text or "") for h in _FOLLOWUP_HINTS)
    persisted = state.get("analysis_filter") if isinstance(state.get("analysis_filter"), dict) else {}
    if persisted.get("role") in FILTER_ROLES and persisted.get("value") and followup:
        return {"role": str(persisted["role"]), "value": str(persisted["value"])}
    if not followup:
        return None
    for turn in reversed(list(state.get("conversation_history") or [])):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        found = _filter_from_text(str(turn.get("content") or ""))
        if found:
            return found
    return None


class AnalysisScope:
    def __init__(
        self,
        wants_defect_rate: bool,
        wants_item_group: bool,
        wants_period: bool,
        wants_equipment: bool,
        equipment_name: str | None,
        item_name: str | None,
        needs_excel: bool,
        process_name: str | None = None,
    ):
        self.wants_defect_rate = wants_defect_rate
        self.wants_item_group = wants_item_group
        self.wants_period = wants_period
        self.wants_equipment = wants_equipment
        self.equipment_name = equipment_name
        self.item_name = item_name
        self.process_name = process_name
        self.needs_excel = needs_excel


def _scoped_excel(excel: Mapping[str, Any]) -> Mapping[str, Any]:
    ctx = excel.get("filter_context") if isinstance(excel.get("filter_context"), dict) else {}
    within = excel.get("within_filter") if isinstance(excel.get("within_filter"), dict) else None
    if ctx.get("matched") and within:
        return within
    return excel


def build_validated_facts(excel: Mapping[str, Any], scope: AnalysisScope | None = None, *, allow_partial: bool = False) -> dict[str, Any]:
    if not excel or excel.get("ok") is False:
        return {}
    overall = excel.get("quality_metrics") if isinstance(excel.get("quality_metrics"), dict) else {}
    scoped = _scoped_excel(excel)
    quality = scoped.get("quality_metrics") if isinstance(scoped.get("quality_metrics"), dict) else {}
    ctx = excel.get("filter_context") if isinstance(excel.get("filter_context"), dict) else {}
    facts: dict[str, Any] = {}
    prod = quality.get("production_qty_total")
    defect = quality.get("defect_qty_total")
    rate = quality.get("defect_rate_pct")
    if prod is not None:
        facts["total_production"] = prod
    if defect is not None:
        facts["total_defects"] = defect
    if rate is not None and prod not in (None, 0) and defect is not None and "불량수량 / 생산수량" in str(quality.get("formula") or ""):
        facts["defect_rate"] = rate
        facts["formula"] = quality.get("formula") or "불량수량 / 생산수량 × 100"
    if ctx.get("matched") and ctx.get("role") and ctx.get("value"):
        facts["scope"] = {"type": ctx.get("role"), "value": ctx.get("matched_value") or ctx.get("value")}
        target = {
            "name": ctx.get("matched_value") or ctx.get("value"),
            "rows": (scoped.get("summary") or {}).get("row_count") if isinstance(scoped.get("summary"), dict) else ctx.get("row_count"),
            "production_qty": prod,
            "defect_qty": defect,
            "defect_rate": rate if "defect_rate" in facts else None,
        }
        facts[str(ctx.get("role"))] = target
        overall_rate = overall.get("defect_rate_pct") if overall else None
        if overall_rate is not None:
            facts["overall"] = {
                "production_qty": overall.get("production_qty_total"),
                "defect_qty": overall.get("defect_qty_total"),
                "defect_rate": overall_rate,
            }
    defects = list(scoped.get("top_defects") or [])
    if defects:
        top = defects[0]
        facts["top_defect"] = {"name": top.get("type"), "count": top.get("defect_qty", top.get("count"))}
    groups = scoped.get("group_analysis") or {}
    items = list(groups.get("by_item") or [])
    if items:
        facts["top_item"] = {"name": items[0].get("key"), "defect_qty": items[0].get("defect_qty"), "defect_rate": items[0].get("defect_rate_pct")}
    if scope and scope.equipment_name and "equipment" not in facts:
        matched = _match_group(list((excel.get("group_analysis") or {}).get("by_equipment") or []), scope.equipment_name)
        if matched:
            facts["equipment"] = {
                "name": matched.get("key"),
                "rows": matched.get("rows"),
                "defect_qty": matched.get("defect_qty"),
                "defect_rate": matched.get("defect_rate_pct"),
            }
    change = scoped.get("change_detection") if isinstance(scoped.get("change_detection"), dict) else {}
    if change.get("previous_period") and change.get("latest_period"):
        facts["period_comparison"] = {
            "previous_period": change.get("previous_period"),
            "latest_period": change.get("latest_period"),
            "defect_qty_delta": change.get("defect_qty_delta"),
            "defect_rate_delta_pp": change.get("defect_rate_delta_pp"),
            "scope": "filtered" if ctx.get("matched") else "overall",
        }
    candidates = list(excel.get("candidate_factors") or [])
    if ctx.get("matched"):
        candidates = list((scoped.get("candidate_factors") if isinstance(scoped, dict) else None) or excel.get("candidate_factors") or [])
    if candidates:
        facts["candidate_factors"] = candidates
    suggestions = list(excel.get("additional_data_suggestions") or [])
    if suggestions:
        facts["additional_data_suggestions"] = suggestions
    if not allow_partial and scope and scope.wants_defect_rate and "defect_rate" not in facts:
        facts.pop("defect_rate", None)
    return facts


def _clarify_message(missing: list[str], scope: AnalysisScope) -> str:
    if "equipment_column" in missing or ("equipment_filter" in missing and "equipment_column" in missing):
        target = scope.equipment_name or "해당 설비"
        return (
            f"현재 데이터에는 설비를 구분할 수 있는 컬럼이 없어 {target}만 따로 분석하기 어렵습니다. "
            "설비명 또는 설비번호가 포함된 데이터가 있으면 추가 분석할 수 있습니다."
        )
    if "equipment_filter" in missing:
        target = scope.equipment_name or "요청한 설비"
        return (
            f"현재 데이터에서 {target} 행을 찾지 못해 해당 설비만 따로 분석하기 어렵습니다. "
            "설비명 표기를 확인하거나 설비 컬럼이 있는 데이터를 업로드해 주세요."
        )
    if "date_column" in missing or "previous_period_comparison" in missing:
        return (
            "현재 데이터에는 비교할 날짜/기간 정보가 부족합니다. "
            "생산일 또는 검사일 컬럼이 포함된 데이터가 있으면 이전 기간과 비교할 수 있습니다."
        )
    if "production_qty" in missing or "defect_rate" in missing:
        return (
            "생산수량 컬럼이 없어 불량률을 계산할 수 없습니다. "
            "생산수량과 불량수량 컬럼이 있으면 품목별 불량률을 분석할 수 있습니다."
        )
    if "item_column" in missing or "item_group" in missing:
        return "품목을 구분할 컬럼이 없어 품목별 집계를 할 수 없습니다. 품목명 컬럼이 있는 데이터를 확인해 주세요."
    if "nonzero_denominator" in missing:
        return "생산수량 합이 0이라 불량률을 계산할 수 없습니다."
    return "현재 데이터만으로 요청하신 분석을 충분히 완료하기 어렵습니다."


def _goal_text(state: Mapping[str, Any]) -> str:
    parts = []
    for key in ("interpreted_goal", "user_message"):
        value = str(state.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def _maybe_expand_top_n(state: Mapping[str, Any], retry_args: dict[str, Any], instruction: str) -> tuple[dict[str, Any], str]:
    current = 5
    for call in state.get("tool_calls") or []:
        if call.get("tool") == "analyze_excel":
            current = int((call.get("args") or {}).get("top_n") or 5)
    if current >= 50:
        return retry_args, instruction
    retry_args = {**retry_args, "top_n": 50}
    return retry_args, instruction or "집계 대상을 넓혀 요청한 품목/설비를 다시 확인"


def _retry_left(state: Mapping[str, Any]) -> bool:
    return int(state.get("retry_count") or 0) < MAX_RETRY


def _latest_excel(results: list[Any]) -> dict[str, Any] | None:
    return _latest_tool_data(results, "analyze_excel")


def _latest_tool(results: list[Any], name: str) -> dict[str, Any] | None:
    for row in reversed(list(results or [])):
        if isinstance(row, dict) and row.get("tool") == name:
            return row
    return None


def _latest_tool_data(results: list[Any], name: str) -> dict[str, Any] | None:
    for row in reversed(list(results or [])):
        if not isinstance(row, dict) or row.get("tool") != name:
            continue
        data = row.get("data")
        if isinstance(data, dict):
            return data
        if row.get("ok") is False:
            return {"ok": False, "error": "tool_error", "detail": row.get("detail") or ""}
    return None


def _plan_wants_report(state: Mapping[str, Any]) -> bool:
    pending = state.get("pending_clarification") if isinstance(state.get("pending_clarification"), dict) else {}
    if pending.get("include_report"):
        return True
    if any(call.get("tool") == "generate_report" for call in (state.get("tool_calls") or [])):
        return True
    return wants_quality_report(_goal_text(state), bool((state.get("documents") or [])))


def _report_step_pending(state: Mapping[str, Any]) -> bool:
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    planned = any(step.get("tool") == "generate_report" for step in (plan.get("steps") or []))
    if not planned:
        return False
    return _latest_tool(list(state.get("execution_results") or []), "generate_report") is None


def _evaluate_excel_for_report(state: Mapping[str, Any], excel: dict[str, Any] | None) -> EvaluationResult:
    if not excel:
        if _retry_left(state):
            return EvaluationResult(
                status="retry",
                checks=[EvaluationCheck(name="analyze_excel_executed", passed=False)],
                missing_requirements=["analyze_excel"],
                retry_instruction="품질 데이터 분석을 먼저 실행",
                retry_target="tools",
            )
        return EvaluationResult(
            status="fail",
            checks=[EvaluationCheck(name="analyze_excel_executed", passed=False)],
            missing_requirements=["analyze_excel"],
            user_message="현재 데이터만으로 보고서를 작성하기 어렵습니다.",
        )
    if excel.get("ok") is False:
        return _tool_failure(state, {"tool": "analyze_excel", "ok": False, "data": excel, "detail": excel.get("detail")})
    if excel.get("needs_clarification"):
        info = excel.get("clarification") or {}
        return EvaluationResult(
            status="clarify",
            checks=[EvaluationCheck(name="needs_clarification", passed=False, detail=info.get("message") or "")],
            missing_requirements=[info.get("field") or "column_mapping"],
        )
    facts = build_validated_facts(excel, infer_analysis_scope(state), allow_partial=True)
    return EvaluationResult(
        status="pass",
        goal_satisfied=True,
        checks=[EvaluationCheck(name="analyze_excel_for_report", passed=True)],
        validated_facts=facts,
    )


def _evaluate_report(state: Mapping[str, Any], row: Mapping[str, Any]) -> EvaluationResult:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    checks: list[EvaluationCheck] = []
    missing: list[str] = []
    created = bool(row.get("ok")) and bool(data.get("ok", row.get("ok")))
    checks.append(EvaluationCheck(name="report_created", passed=created, detail=str(data.get("detail") or row.get("detail") or "")))
    if not created:
        missing.append("report_file")
        return EvaluationResult(
            status="fail",
            checks=checks,
            missing_requirements=missing,
            user_message=str(data.get("detail") or "보고서를 생성하지 못했습니다."),
            validated_facts=dict(state.get("validated_facts") or {}),
        )
    path = Path(str(data.get("stored_path") or ""))
    exists = bool(str(data.get("stored_path") or "")) and path.exists()
    checks.append(EvaluationCheck(name="report_file_exists", passed=exists))
    if not exists:
        missing.append("report_file")
    included = set(data.get("sections_included") or [])
    required = {"분석 개요", "전체 품질 현황", "주요 확인사항", "추가 확인 필요사항"}
    section_ok = required.issubset(included)
    checks.append(EvaluationCheck(name="required_sections", passed=section_ok, detail=", ".join(sorted(required - included))))
    if not section_ok:
        missing.append("report_sections")
    facts = dict(state.get("validated_facts") or {})
    used = data.get("used_facts") if isinstance(data.get("used_facts"), dict) else {}
    mismatch = False
    for key in ("total_production", "total_defects", "defect_rate"):
        if key in facts and key in used and facts.get(key) != used.get(key):
            mismatch = True
    checks.append(EvaluationCheck(name="report_numbers_match_facts", passed=not mismatch))
    if mismatch:
        missing.append("report_numbers")
    if missing:
        return EvaluationResult(
            status="fail",
            checks=checks,
            missing_requirements=missing,
            user_message="보고서를 생성했지만 검증을 통과하지 못했습니다.",
            validated_facts=facts,
        )
    return EvaluationResult(
        status="pass",
        goal_satisfied=True,
        checks=checks,
        validated_facts=facts,
    )


def _mapped(mapping: Mapping[str, Any], role: str) -> str | None:
    info = mapping.get(role) if isinstance(mapping, dict) else None
    if isinstance(info, dict):
        col = info.get("column")
        return str(col) if col else None
    return None


def _match_group(rows: list[Mapping[str, Any]], name: str) -> dict[str, Any] | None:
    if not name:
        return None
    needle = _norm(name)
    if not needle:
        return None
    for row in rows:
        key = _norm(row.get("key") or "")
        if needle == key or needle in key or key in needle:
            return dict(row)
    return None


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()
