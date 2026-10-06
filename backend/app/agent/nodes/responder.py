import json
from datetime import datetime, timezone

from sqlalchemy.orm.attributes import flag_modified

from app.agent.excel_reply import (
    compact_excel_payload,
    excel_payload_from_state,
    format_excel_reply,
    pending_from_state,
)
from app.agent.prompts import RESPOND_SYSTEM
from app.agent.state import AgentState
from app.db.database import SessionLocal
from app.models import AgentSession, Message


def fallback_reply(state: AgentState) -> str:
    status = state.get("evaluation_status") or ""
    eval_msg = (state.get("evaluation_user_message") or "").strip()
    if status in {"clarify", "fail"} and eval_msg:
        return eval_msg
    if state.get("intent_clarification"):
        return str(state.get("intent_clarification"))
    report_text = format_report_reply(state)
    if report_text and status == "pass":
        return report_text
    facts = state.get("validated_facts") if isinstance(state.get("validated_facts"), dict) else {}
    if status == "pass" and facts:
        fact_text = format_validated_facts(facts, user_message=state.get("user_message") or "")
        if fact_text:
            return fact_text
    excel_text = format_excel_reply(excel_payload_from_state(state), plan=state.get("plan"))
    if excel_text:
        return excel_text
    plan = state.get("plan") or {}
    if plan.get("clarification"):
        return str(plan.get("clarification"))
    if eval_msg:
        return eval_msg
    actions = state.get("actions") or []
    remaining = [a for a in actions if a.get("status") not in {"COMPLETED"}]
    if state.get("intent") == "query":
        if not remaining:
            return "등록된 남은 업무가 없습니다. 모든 Action이 완료 상태입니다."
        lines = ["아직 끝나지 않은 업무입니다."]
        for action in remaining:
            lines.append(
                f"- [{action.get('priority')}] {action.get('title')} "
                f"(마감 {action.get('deadline') or '-'}, {action.get('status')})"
            )
        return "\n".join(lines)
    n = len(actions)
    tools = state.get("execution_results") or []
    tool_txt = ", ".join(t.get("detail") or t.get("tool") for t in tools) or "없음"
    return (
        f"문서를 분석해 Action {n}건을 추출하고 업무 계획을 세웠습니다.\n"
        f"실행 결과: {tool_txt}\n"
        "우측 패널에서 우선순위와 마감을 확인하고, 남은 일은 언제든 물어보세요."
    )


def format_report_reply(state: AgentState) -> str | None:
    for row in reversed(list(state.get("execution_results") or [])):
        if row.get("tool") != "generate_report":
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        if not row.get("ok") or not data.get("ok", True):
            return str(data.get("detail") or row.get("detail") or "보고서를 생성하지 못했습니다.")
        filename = data.get("filename") or "주간 품질 현황 보고서.docx"
        download = data.get("download_path") or ""
        lines = ["품질 현황 보고서가 생성되었습니다.", str(filename)]
        if download:
            lines.append(f"다운로드: {download}")
        else:
            lines.append("다운로드")
        return "\n".join(lines)
    return None


def format_validated_facts(facts: dict, user_message: str = "") -> str | None:
    if not facts:
        return None
    lines: list[str] = []
    scope = facts.get("scope") if isinstance(facts.get("scope"), dict) else {}
    target = str(scope.get("value") or "")
    equipment = facts.get("equipment") if isinstance(facts.get("equipment"), dict) else None
    item = facts.get("item") if isinstance(facts.get("item"), dict) else None
    process = facts.get("process") if isinstance(facts.get("process"), dict) else None
    focused = equipment or item or process
    prod = facts.get("total_production")
    defect = facts.get("total_defects")
    rate = facts.get("defect_rate")
    if focused and target:
        extra = f", 불량률 {focused.get('defect_rate')}%" if focused.get("defect_rate") is not None else ""
        if focused.get("production_qty") is not None and focused.get("defect_qty") is not None:
            lines.append(
                f"{focused.get('name') or target} 생산수량은 {focused.get('production_qty')}이며 "
                f"불량은 {focused.get('defect_qty')}건입니다{extra}."
            )
        else:
            lines.append(f"{focused.get('name') or target} 기준 {focused.get('rows')}건이 확인되었습니다{extra}.")
        overall = facts.get("overall") if isinstance(facts.get("overall"), dict) else None
        if overall and overall.get("defect_rate") is not None and focused.get("defect_rate") is not None:
            lines.append(f"전체 평균 불량률은 {overall.get('defect_rate')}%입니다.")
    elif prod is not None and defect is not None and rate is not None:
        lines.append(f"전체 생산수량은 {prod}이며 불량은 {defect}건으로, 불량률은 {rate}%입니다.")
    elif prod is not None and defect is not None:
        lines.append(f"전체 생산수량은 {prod}이며 불량은 {defect}건입니다. 불량률은 계산하지 않았습니다.")

    period = facts.get("period_comparison") if isinstance(facts.get("period_comparison"), dict) else None
    if period and period.get("previous_period") and period.get("latest_period"):
        prefix = f"{target}의 " if target and period.get("scope") == "filtered" else ""
        overall_note = period.get("scope") == "overall" and target
        delta = period.get("defect_rate_delta_pp")
        if delta is not None:
            direction = "높아졌" if delta > 0 else "낮아졌" if delta < 0 else "같았"
            line = f"{prefix}{period['previous_period']} 대비 {period['latest_period']} 불량률은 {direction}습니다."
            if overall_note:
                line = "참고로 전체 데이터 기준으로는 " + line
            lines.append(line)
        elif period.get("defect_qty_delta") is not None:
            delta_q = period["defect_qty_delta"]
            direction = "증가" if delta_q > 0 else "감소" if delta_q < 0 else "동일"
            line = f"{prefix}{period['previous_period']} 대비 {period['latest_period']} 불량수량은 {direction}했습니다."
            if overall_note:
                line = "참고로 전체 데이터 기준으로는 " + line
            lines.append(line)

    top = facts.get("top_defect") if isinstance(facts.get("top_defect"), dict) else None
    top_item = facts.get("top_item") if isinstance(facts.get("top_item"), dict) else None
    factors = list(facts.get("candidate_factors") or [])
    if factors:
        lines.append("원인으로 확정할 수는 없지만 우선 확인할 후보는 다음과 같습니다.")
        for item in factors[:4]:
            name = item.get("key")
            if item.get("kind") == "defect_share" and item.get("share_pct") is not None:
                lines.append(f"- {name}이 해당 범위 전체 불량의 {item.get('share_pct')}%를 차지합니다.")
            elif item.get("defect_rate_pct") is not None:
                lines.append(f"- {name}의 불량률이 {item.get('defect_rate_pct')}%로 해당 범위에서 높게 나타납니다.")
            elif item.get("share_pct") is not None:
                lines.append(f"- {name} 비중이 {item.get('share_pct')}%입니다.")
            else:
                lines.append(f"- {name}을 우선 점검할 필요가 있습니다.")
        lines.append("위 항목은 함께 관찰된 현상이며 인과관계로 단정하지 않았습니다.")
    else:
        if top and top.get("name") is not None and not target:
            lines.append(f"가장 많이 발생한 불량은 {top.get('name')} {top.get('count')}건입니다.")
        if top_item and top_item.get("name") and not target:
            lines.append(f"품목 기준으로는 {top_item.get('name')}에서 불량이 가장 많습니다.")
        if _looks_like_cause_question(user_message):
            lines.append("현재 파일에서 원인을 더 좁힐 항목은 확인되지 않았습니다.")

    suggestions = list(facts.get("additional_data_suggestions") or [])
    if suggestions and (target or _looks_like_cause_question(user_message) or factors):
        lines.append("현재 파일에 없는 관련 데이터로는 " + ", ".join(suggestions) + "을 함께 비교해보는 것이 좋습니다.")
    if not lines:
        return None
    return "\n".join(lines)


def _looks_like_cause_question(message: str) -> bool:
    text = message or ""
    return any(k in text for k in ("왜", "원인", "상태", "어떤거", "어때"))


def build_reply_messages(state: AgentState) -> list[dict]:
    payload = excel_payload_from_state(state)
    compact = compact_excel_payload(payload) if payload else None
    return [
        {"role": "system", "content": RESPOND_SYSTEM},
        {
            "role": "user",
            "content": (
                f"사용자 메시지: {state.get('user_message')}\n"
                f"intent: {state.get('intent')}\n"
                f"interpreted_goal: {state.get('interpreted_goal')}\n"
                f"intent_clarification: {state.get('intent_clarification')}\n"
                f"evaluation_status: {state.get('evaluation_status')}\n"
                f"evaluation_user_message: {state.get('evaluation_user_message')}\n"
                f"validated_facts: {json.dumps(state.get('validated_facts') or {}, ensure_ascii=False)}\n"
                f"missing_requirements: {state.get('missing_requirements')}\n"
                f"plan: {state.get('plan')}\n"
                f"요약: {state.get('document_summary')}\n"
                f"Actions: {state.get('actions')}\n"
                f"Tasks: {state.get('tasks')}\n"
                f"실행결과: {state.get('execution_results')}\n"
                f"검증: {state.get('verification_notes')}\n"
                f"EXCEL_JSON: {json.dumps(compact, ensure_ascii=False) if compact else 'null'}"
            ),
        },
    ]


def persist_turn(session_id: str, user_message: str, reply: str) -> str | None:
    db = SessionLocal()
    try:
        session = db.get(AgentSession, session_id)
        if not session:
            return None
        db.add(Message(session_id=session.id, role="user", content=user_message or ""))
        db.add(Message(session_id=session.id, role="assistant", content=reply))
        snap = dict(session.state_json or {})
        history = list(snap.get("conversation_history") or [])
        history.append({"role": "user", "content": user_message or ""})
        history.append({"role": "assistant", "content": reply})
        snap["conversation_history"] = history
        snap["assistant_reply"] = reply
        session.state_json = snap
        flag_modified(session, "state_json")
        session.updated_at = datetime.now(timezone.utc)
        db.commit()
        return session.title
    finally:
        db.close()


def responder(state: AgentState) -> dict:
    payload = excel_payload_from_state(state)
    pending = pending_from_state(state)
    if pending is None:
        plan = state.get("plan") or {}
        if plan.get("needs_clarification"):
            pending = state.get("pending_clarification")
    return {
        "current_step": "responder",
        "pending_clarification": pending,
    }
