"""analyze_excel 구조화 결과를 실무자용 한국어 설명으로 바꾼다."""

from __future__ import annotations

from typing import Any, Mapping


def excel_payload_from_state(state: Mapping[str, Any]) -> dict[str, Any] | None:
    for row in reversed(list(state.get("execution_results") or [])):
        if row.get("tool") != "analyze_excel":
            continue
        data = row.get("data")
        if isinstance(data, dict):
            return data
    return None


def compact_excel_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": data.get("ok"),
        "detail": data.get("detail"),
        "file": data.get("file"),
        "schema": {
            "selected_sheet": (data.get("schema") or {}).get("selected_sheet"),
            "row_count": (data.get("schema") or {}).get("row_count"),
            "column_count": (data.get("schema") or {}).get("column_count"),
            "columns": (data.get("schema") or {}).get("columns"),
        }
        if data.get("schema")
        else None,
        "column_mapping": data.get("column_mapping"),
        "summary": data.get("summary"),
        "quality_metrics": data.get("quality_metrics"),
        "top_defects": data.get("top_defects"),
        "group_analysis": data.get("group_analysis"),
        "period_analysis": data.get("period_analysis"),
        "change_detection": data.get("change_detection"),
        "filter_context": data.get("filter_context"),
        "within_filter": _compact_within(data.get("within_filter")),
        "candidate_factors": data.get("candidate_factors") or [],
        "additional_data_suggestions": data.get("additional_data_suggestions") or [],
        "warnings": data.get("warnings"),
        "needs_clarification": data.get("needs_clarification"),
        "clarification": data.get("clarification"),
        "error": data.get("error"),
    }


def _compact_within(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {
        "quality_metrics": payload.get("quality_metrics"),
        "top_defects": payload.get("top_defects"),
        "group_analysis": payload.get("group_analysis"),
        "period_analysis": payload.get("period_analysis"),
        "change_detection": payload.get("change_detection"),
        "candidate_factors": payload.get("candidate_factors") or [],
        "summary": payload.get("summary"),
    }


def pending_from_excel(
    data: Mapping[str, Any] | None,
    *,
    resolved_mapping: Mapping[str, Any] | None = None,
    source_document_id: str | None = None,
) -> dict[str, Any] | None:
    if not data or not data.get("needs_clarification"):
        return None
    clarification = data.get("clarification") or {}
    file_info = data.get("file") or {}
    document_id = (file_info.get("id") if isinstance(file_info, dict) else "") or ""
    return {
        "type": "column",
        "tool": "analyze_excel",
        "document_id": document_id,
        "field": clarification.get("field") or "",
        "candidates": list(clarification.get("candidates") or []),
        "message": clarification.get("message") or data.get("detail") or "",
        "resolved_mapping": mapping_for_document(resolved_mapping, document_id, source_document_id),
    }


def pending_from_state(state: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = excel_payload_from_state(state)
    if not payload:
        return None
    document_id = str(((payload.get("file") if isinstance(payload.get("file"), dict) else {}) or {}).get("id") or "")
    pending = pending_from_excel(
        payload,
        resolved_mapping=explicit_column_mapping_from_state(state, document_id),
    )
    if pending and _pending_wants_report(state):
        pending["include_report"] = True
    return pending


def _pending_wants_report(state: Mapping[str, Any]) -> bool:
    existing = state.get("pending_clarification") if isinstance(state.get("pending_clarification"), dict) else {}
    if existing.get("include_report"):
        return True
    from app.agent.work_intent import tabular_documents, wants_quality_report

    text = f"{state.get('user_message') or ''} {state.get('interpreted_goal') or ''}"
    return wants_quality_report(text, bool(tabular_documents(state)))


def merge_resolved_mapping(existing: Mapping[str, Any] | None, field: str, column: str) -> dict[str, str]:
    merged = _clean_mapping(existing)
    if field and column:
        merged[str(field)] = str(column)
    return merged


def mapping_for_document(mapping: Mapping[str, Any] | None, document_id: str, source_document_id: str | None = None) -> dict[str, str]:
    if source_document_id and document_id and str(source_document_id) != str(document_id):
        return {}
    return _clean_mapping(mapping)


def persistent_mapping_store(state: Mapping[str, Any] | None) -> dict[str, dict[str, str]]:
    raw = (state or {}).get("document_column_mappings") if isinstance((state or {}).get("document_column_mappings"), dict) else {}
    store: dict[str, dict[str, str]] = {}
    for key, value in dict(raw or {}).items():
        doc_id = str(key or "").strip()
        cleaned = _clean_mapping(value if isinstance(value, Mapping) else {})
        if doc_id and cleaned:
            store[doc_id] = cleaned
    return store


def persistent_mapping_for_document(state: Mapping[str, Any] | None, document_id: str) -> dict[str, str]:
    doc_id = str(document_id or "").strip()
    if not doc_id:
        return {}
    return dict(persistent_mapping_store(state).get(doc_id) or {})


def remember_document_mapping(
    state: Mapping[str, Any] | None, document_id: str, mapping: Mapping[str, Any] | None
) -> dict[str, dict[str, str]]:
    store = persistent_mapping_store(state)
    doc_id = str(document_id or "").strip()
    incoming = _clean_mapping(mapping)
    if not doc_id or not incoming:
        return store
    current = dict(store.get(doc_id) or {})
    current.update(incoming)
    store[doc_id] = current
    return store


def drop_stale_document_mapping(
    state: Mapping[str, Any] | None, document_id: str, stale_roles: list[str] | tuple[str, ...] | None
) -> dict[str, dict[str, str]]:
    store = persistent_mapping_store(state)
    doc_id = str(document_id or "").strip()
    if not doc_id or not stale_roles:
        return store
    current = dict(store.get(doc_id) or {})
    for role in stale_roles:
        current.pop(str(role or ""), None)
    if current:
        store[doc_id] = current
    else:
        store.pop(doc_id, None)
    return store


def explicit_column_mapping_from_state(state: Mapping[str, Any], document_id: str) -> dict[str, str]:
    mapping: dict[str, str] = dict(persistent_mapping_for_document(state, document_id))
    pending = state.get("pending_clarification") if isinstance(state.get("pending_clarification"), dict) else None
    if pending and pending.get("type") == "column":
        mapping.update(
            mapping_for_document(
                pending.get("resolved_mapping"),
                document_id,
                str(pending.get("document_id") or "") or None,
            )
        )
    for call in state.get("tool_calls") or []:
        if call.get("tool") != "analyze_excel":
            continue
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        mapping.update(
            mapping_for_document(
                args.get("column_mapping"),
                document_id,
                str(args.get("document_id") or "") or None,
            )
        )
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    for step in plan.get("steps") or []:
        if not isinstance(step, dict) or step.get("tool") != "analyze_excel":
            continue
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        step_doc = str(step.get("document_id") or args.get("document_id") or "")
        mapping.update(mapping_for_document(args.get("column_mapping"), document_id, step_doc or None))
    return mapping


def _clean_mapping(mapping: Mapping[str, Any] | None) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, value in dict(mapping or {}).items():
        role = str(key or "").strip()
        column = str(value or "").strip()
        if role and column:
            cleaned[role] = column
    return cleaned


def format_excel_reply(data: Mapping[str, Any] | None, *, plan: Mapping[str, Any] | None = None) -> str | None:
    if plan and plan.get("needs_clarification") and not data:
        return str(plan.get("clarification") or plan.get("goal") or "").strip() or None
    if not data:
        return None
    if data.get("needs_clarification"):
        return _format_clarification(data)
    if not data.get("ok"):
        return str(data.get("detail") or "Excel 파일을 분석하지 못했습니다.")
    return _format_metrics(data)


def _format_clarification(data: Mapping[str, Any]) -> str:
    info = data.get("clarification") or {}
    message = info.get("message") or data.get("detail") or "컬럼을 확정할 수 없습니다."
    candidates = list(info.get("candidates") or [])
    lines = [message, ""]
    if candidates:
        lines.append("다음 중 어떤 컬럼이 실제 값인가요?")
        for idx, name in enumerate(candidates, 1):
            lines.append(f"{idx}. {name}")
        lines.append("")
        lines.append("번호 또는 컬럼명을 답해 주시면 그 컬럼으로 다시 분석합니다.")
    return "\n".join(lines).strip()


def _format_metrics(data: Mapping[str, Any]) -> str:
    ctx = data.get("filter_context") if isinstance(data.get("filter_context"), dict) else {}
    scoped = data.get("within_filter") if ctx.get("matched") and isinstance(data.get("within_filter"), dict) else data
    metrics = scoped.get("quality_metrics") or {}
    overall = data.get("quality_metrics") or {}
    file_info = data.get("file") or {}
    filename = file_info.get("filename") if isinstance(file_info, dict) else ""
    lines: list[str] = []
    if filename:
        lines.append(f"{filename} 분석 결과입니다.")
    label = str(ctx.get("matched_value") or ctx.get("value") or "")
    prod = metrics.get("production_qty_total")
    defect = metrics.get("defect_qty_total")
    rate = metrics.get("defect_rate_pct")
    if label and ctx.get("matched"):
        if prod is not None and defect is not None and rate is not None:
            lines.append(f"{label} 생산수량은 {prod}이며 불량은 {defect}건으로, 불량률은 {rate}%입니다.")
        overall_rate = overall.get("defect_rate_pct")
        if overall_rate is not None and rate is not None:
            lines.append(f"전체 평균 불량률 {overall_rate}%와 비교한 수치입니다.")
    elif prod is not None and defect is not None and rate is not None:
        lines.append(f"전체 생산수량은 {prod}이며 불량은 {defect}건으로, 불량률은 {rate}%입니다.")
    elif metrics.get("ng_count") is not None:
        lines.append(
            f"검사 {metrics.get('inspected_rows')}건 중 NG {metrics.get('ng_count')}건입니다."
            + (
                f" 검사 불합격률은 {metrics.get('inspection_fail_rate_pct') }%입니다."
                if metrics.get("inspection_fail_rate_pct") is not None
                else ""
            )
        )
    elif (data.get("schema") or {}).get("row_count"):
        schema = data.get("schema") or {}
        lines.append(f"데이터는 {schema.get('row_count')}행 {schema.get('column_count')}열입니다.")

    scoped_change = scoped.get("change_detection") or {}
    if scoped_change.get("previous_period") and scoped_change.get("latest_period"):
        delta = scoped_change.get("defect_rate_delta_pp")
        if delta is not None:
            direction = "높아졌" if delta > 0 else "낮아졌" if delta < 0 else "같았"
            prefix = f"{label}의 " if label and ctx.get("matched") else ""
            lines.append(
                f"{prefix}{scoped_change.get('previous_period')} 대비 {scoped_change.get('latest_period')} 불량률은 {direction}습니다."
            )
        elif scoped_change.get("defect_qty_delta") is not None:
            delta_q = scoped_change["defect_qty_delta"]
            direction = "증가" if delta_q > 0 else "감소" if delta_q < 0 else "동일"
            prefix = f"{label}의 " if label and ctx.get("matched") else ""
            lines.append(
                f"{prefix}{scoped_change.get('previous_period')} 대비 {scoped_change.get('latest_period')} 불량수량은 {direction}했습니다."
            )

    factors = list(scoped.get("candidate_factors") or data.get("candidate_factors") or [])
    if factors:
        lines.append("원인으로 확정할 수는 없지만 우선 확인할 후보는 다음과 같습니다.")
        for item in factors[:4]:
            lines.append(f"- {_factor_line(item)}")
        lines.append("위 항목은 함께 관찰된 현상이며 인과관계로 단정하지 않았습니다.")
    else:
        defects = list(scoped.get("top_defects") or [])
        if defects:
            top = defects[0]
            qty = top.get("defect_qty", top.get("count"))
            lines.append(f"가장 많이 발생한 불량은 {top.get('type')} {qty}건입니다.")
        groups = (scoped.get("group_analysis") or {}).get("by_item") or []
        if groups:
            lines.append(f"품목 기준으로는 {groups[0].get('key')}에서 불량이 가장 많습니다.")

    suggestions = list(data.get("additional_data_suggestions") or [])
    if suggestions and (ctx.get("matched") or factors):
        lines.append("현재 파일에서 확인되지 않는 관련 데이터로는 " + ", ".join(suggestions) + "이 있습니다.")
    warnings = [w for w in (data.get("warnings") or []) if w]
    if warnings:
        lines.append("참고: " + warnings[0])
    return "\n".join(lines).strip()


def _factor_line(item: Mapping[str, Any]) -> str:
    name = item.get("key") or ""
    role = item.get("role") or ""
    rate = item.get("defect_rate_pct")
    share = item.get("share_pct")
    if item.get("kind") == "defect_share" and share is not None:
        return f"{name} 불량이 해당 범위 전체 불량의 {share}%를 차지합니다."
    if rate is not None and item.get("baseline_rate_pct") is not None:
        return f"{name}의 불량률이 {rate}%로 해당 범위 평균보다 높습니다."
    if share is not None:
        return f"{name}({role}) 비중이 {share}%로 높습니다."
    return f"{name}을 우선 점검할 필요가 있습니다."
