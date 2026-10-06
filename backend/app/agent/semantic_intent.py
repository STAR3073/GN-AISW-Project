"""Rule Fast Path + LLM Semantic Intent. Tool은 실행하지 않는다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from app.agent.prompts import SEMANTIC_CLARIFICATION_SYSTEM, SEMANTIC_INTENT_SYSTEM
from app.agent.work_intent import classify_rule_intent, iter_documents, tabular_documents
from app.config import get_settings
from app.schemas.agent import ClarificationChoice, SemanticIntent
from app.services.llm_service import get_llm

ALLOWED_INTENTS = {"query", "execute", "ingest"}
ALLOWED_CAPABILITIES = {
    "tabular_data_analysis",
    "document_ingest",
    "document_query",
    "draft",
    "none",
}
MIN_CONFIDENCE = 0.6
MIN_CLARIFICATION_CONFIDENCE = 0.75


def build_intent_context(state: Mapping[str, Any]) -> dict[str, Any]:
    files = []
    for row in iter_documents(state):
        name = row.get("filename") or ""
        suffix = Path(name).suffix.lower()
        files.append(
            {
                "id": row.get("id") or "",
                "filename": name,
                "kind": "tabular" if suffix in {".xlsx", ".xls", ".csv"} else "document",
                "suffix": suffix,
            }
        )
    history = []
    for turn in list(state.get("conversation_history") or [])[-4:]:
        if not isinstance(turn, dict):
            continue
        history.append(
            {
                "role": turn.get("role") or "",
                "content": str(turn.get("content") or "")[:200],
            }
        )
    schema_columns = []
    for result in reversed(list(state.get("execution_results") or [])):
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict) and (data.get("schema") or {}).get("columns"):
            schema_columns = list((data.get("schema") or {}).get("columns") or [])[:40]
            break
    pending = state.get("pending_clarification")
    return {
        "user_message": state.get("user_message") or "",
        "has_tabular": bool(tabular_documents(state)),
        "tabular_count": len(tabular_documents(state)),
        "files": files,
        "has_prose_document": any(f.get("kind") == "document" for f in files),
        "has_actions": bool(state.get("actions")),
        "pending_clarification": bool(pending),
        "pending_type": (pending or {}).get("type") if isinstance(pending, dict) else None,
        "known_schema_columns": schema_columns,
        "recent_turns": history,
    }


def call_semantic_intent(state: Mapping[str, Any]) -> SemanticIntent:
    context = build_intent_context(state)
    llm = get_llm()
    settings = get_settings()
    return llm.structured(
        [
            {"role": "system", "content": SEMANTIC_INTENT_SYSTEM},
            {
                "role": "user",
                "content": "세션 컨텍스트(메타데이터만):\n" + json.dumps(context, ensure_ascii=False),
            },
        ],
        SemanticIntent,
        model=settings.llm_extract_model,
    )


def resolve_semantic_clarification(message: str, candidates: list[Any]) -> Any | None:
    """규칙으로 후보를 확정하지 못했을 때만 호출한다. 낮은 confidence는 고르지 않는다."""
    if not message or not candidates:
        return None
    labels = []
    for item in candidates:
        if isinstance(item, dict):
            labels.append(str(item.get("filename") or item.get("id") or ""))
        else:
            labels.append(str(item or ""))
    payload = {
        "user_message": message,
        "candidates": [{"index": idx, "label": label} for idx, label in enumerate(labels)],
    }
    try:
        llm = get_llm()
        settings = get_settings()
        choice = llm.structured(
            [
                {"role": "system", "content": SEMANTIC_CLARIFICATION_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            ClarificationChoice,
            model=settings.llm_extract_model,
        )
    except Exception:
        return None
    if choice.selected_index is None or float(choice.confidence or 0) < MIN_CLARIFICATION_CONFIDENCE:
        return None
    index = int(choice.selected_index)
    if 0 <= index < len(candidates):
        return candidates[index]
    return None


def resolve_hybrid_intent(state: Mapping[str, Any]) -> dict[str, Any]:
    rule = classify_rule_intent(state)
    if rule["verdict"] != "UNCERTAIN" and rule.get("intent") in ALLOWED_INTENTS:
        return _pack(
            intent=rule["intent"],
            goal=rule.get("goal") or "",
            source="rule",
            confidence=float(rule.get("confidence") or 0.9),
            capability=rule.get("capability") or "none",
            requires_tool=rule["intent"] == "execute",
            reason=rule.get("reason") or "",
            clarification="",
        )
    try:
        semantic = call_semantic_intent(state)
    except Exception:
        return _uncertain_query("요청을 확실히 이해하지 못했습니다. 어떤 내용을 확인해드릴까요?")
    return _from_semantic(semantic, state)


def _from_semantic(semantic: SemanticIntent, state: Mapping[str, Any]) -> dict[str, Any]:
    intent = semantic.intent if semantic.intent in ALLOWED_INTENTS else "query"
    capability = semantic.suggested_capability if semantic.suggested_capability in ALLOWED_CAPABILITIES else "none"
    confidence = float(semantic.confidence or 0)
    requires_tool = bool(semantic.requires_tool) and intent == "execute"
    clarification = (semantic.clarification or "").strip()
    has_tabular = bool(tabular_documents(state))
    files = tabular_documents(state)

    if confidence < MIN_CONFIDENCE:
        return _uncertain_query(
            clarification
            or _default_clarification(has_tabular, files, state.get("user_message") or "")
        )

    if intent == "execute" and capability == "tabular_data_analysis" and not files:
        requires_tool = False
        if confidence < 0.85:
            return _uncertain_query(
                clarification
                or "분석할 Excel/CSV 파일이 없습니다. 파일을 업로드하거나 어떤 자료를 볼지 알려 주세요."
            )

    return _pack(
        intent=intent,
        goal=semantic.goal or "",
        source="semantic",
        confidence=confidence,
        capability=capability,
        requires_tool=requires_tool and intent == "execute",
        reason=semantic.reason or "",
        clarification=clarification,
    )


def _default_clarification(has_tabular: bool, files: list[dict[str, Any]], message: str) -> str:
    if len(files) > 1:
        names = ", ".join(str(f.get("filename") or "") for f in files)
        return f"현재 Excel/CSV 파일이 여러 개입니다({names}). 어떤 파일을 확인할까요?"
    if has_tabular:
        name = files[0].get("filename") if files else "데이터 파일"
        return f"현재 {name}이 선택되어 있습니다. 이 데이터를 분석해드릴까요?"
    return "어떤 내용을 확인해드릴까요?"


def _uncertain_query(clarification: str) -> dict[str, Any]:
    return _pack(
        intent="query",
        goal="",
        source="semantic",
        confidence=0.4,
        capability="none",
        requires_tool=False,
        reason="confidence_low",
        clarification=clarification,
    )


def _pack(
    *,
    intent: str,
    goal: str,
    source: str,
    confidence: float,
    capability: str,
    requires_tool: bool,
    reason: str,
    clarification: str,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "interpreted_goal": goal,
        "intent_source": source,
        "intent_confidence": confidence,
        "suggested_capability": capability,
        "requires_tool": requires_tool,
        "intent_reason": reason,
        "intent_clarification": clarification,
    }
