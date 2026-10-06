from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    session_id: str
    user_message: str
    intent: str
    document: dict[str, Any]
    documents: list[dict[str, Any]]
    document_summary: str
    requirements: list[Any]
    actions: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    execution_results: list[dict[str, Any]]
    conversation_history: list[dict[str, str]]
    current_step: str
    activity_log: list[dict[str, Any]]
    retry_count: int
    verification_ok: bool
    verification_notes: str
    assistant_reply: str
    error: str
    understanding: dict[str, Any]
    plan: dict[str, Any]
    plan_steps: list[dict[str, Any]]
    pending_clarification: dict[str, Any] | None
    document_column_mappings: dict[str, dict[str, str]]
    analysis_filter: dict[str, str]
    interpreted_goal: str
    intent_source: str
    intent_confidence: float
    suggested_capability: str
    intent_clarification: str
    intent_reason: str
    evaluation_status: str
    evaluation_checks: list[dict[str, Any]]
    evaluation_count: int
    missing_requirements: list[str]
    validated_facts: dict[str, Any]
    evaluation_retry_args: dict[str, Any]
    evaluation_retry_target: str
    evaluation_user_message: str


def empty_state() -> AgentState:
    return {
        "session_id": "",
        "user_message": "",
        "intent": "",
        "document": {},
        "documents": [],
        "document_summary": "",
        "requirements": [],
        "actions": [],
        "tasks": [],
        "tool_calls": [],
        "execution_results": [],
        "conversation_history": [],
        "current_step": "",
        "activity_log": [],
        "retry_count": 0,
        "verification_ok": True,
        "verification_notes": "",
        "assistant_reply": "",
        "error": "",
        "understanding": {},
        "plan": {},
        "plan_steps": [],
        "pending_clarification": None,
        "document_column_mappings": {},
        "analysis_filter": {},
        "interpreted_goal": "",
        "intent_source": "",
        "intent_confidence": 0.0,
        "suggested_capability": "",
        "intent_clarification": "",
        "intent_reason": "",
        "evaluation_status": "",
        "evaluation_checks": [],
        "evaluation_count": 0,
        "missing_requirements": [],
        "validated_facts": {},
        "evaluation_retry_args": {},
        "evaluation_retry_target": "",
        "evaluation_user_message": "",
    }
