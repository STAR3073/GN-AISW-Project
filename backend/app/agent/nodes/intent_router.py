from app.agent.semantic_intent import resolve_hybrid_intent
from app.agent.state import AgentState


def intent_router(state: AgentState) -> dict:
    resolved = resolve_hybrid_intent(state)
    return {
        **resolved,
        "current_step": "intent_router",
        "retry_count": 0,
        "evaluation_count": 0,
        "evaluation_status": "",
        "evaluation_checks": [],
        "missing_requirements": [],
        "validated_facts": {},
        "evaluation_retry_args": {},
        "evaluation_retry_target": "",
        "evaluation_user_message": "",
        "error": "",
    }
