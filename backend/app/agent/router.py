from app.agent.evaluator import MAX_RETRY
from app.agent.semantic_intent import resolve_hybrid_intent


def resolve_intent(state: dict) -> str:
    return str(resolve_hybrid_intent(state).get("intent") or "query")


def route_from_state(state: dict) -> str:
    return state.get("intent") or resolve_intent(state)


def after_verify(state: dict) -> str:
    status = state.get("evaluation_status")
    if status == "continue":
        return "continue_tools"
    if status == "retry":
        target = state.get("evaluation_retry_target") or "tools"
        if target == "planner":
            return "retry_plan"
        return "retry_tools"
    if status in {"pass", "clarify", "fail"}:
        return "ok"
    if not state.get("verification_ok", True) and int(state.get("retry_count") or 0) < MAX_RETRY:
        return "retry_tools"
    return "ok"


def after_plan(state: dict) -> str:
    plan = state.get("plan") or {}
    if plan.get("requires_tools"):
        steps = plan.get("steps") or []
        if any(step.get("tool") for step in steps):
            return "tools"
    return "reply"
