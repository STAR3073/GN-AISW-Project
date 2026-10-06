from app.agent.evaluator import MAX_RETRY, evaluate
from app.agent.state import AgentState
from app.schemas.agent import EvaluationResult


def verifier(state: AgentState) -> dict:
    result = evaluate(state)
    count = int(state.get("evaluation_count") or 0) + 1
    retry = int(state.get("retry_count") or 0)

    if result.status == "continue":
        notes = [c.detail or c.name for c in result.checks if not c.passed] or [c.name for c in result.checks]
        return {
            "verification_ok": True,
            "verification_notes": " / ".join(notes),
            "evaluation_status": "continue",
            "evaluation_checks": [c.model_dump() for c in result.checks],
            "evaluation_count": count,
            "missing_requirements": list(result.missing_requirements),
            "validated_facts": dict(result.validated_facts or {}),
            "evaluation_user_message": result.user_message or "",
            "evaluation_retry_target": "tools",
            "evaluation_retry_args": {},
            "retry_count": retry,
            "current_step": "verifier",
        }

    if result.status == "retry" and retry >= MAX_RETRY:
        result = EvaluationResult(
            status="fail",
            goal_satisfied=False,
            checks=result.checks,
            missing_requirements=result.missing_requirements,
            user_message=result.user_message
            or "현재 데이터만으로 요청하신 분석을 충분히 완료하기 어렵습니다.",
            validated_facts=result.validated_facts,
        )

    notes = [c.detail or c.name for c in result.checks if not c.passed] or [c.name for c in result.checks]
    updates: dict = {
        "verification_ok": result.status == "pass",
        "verification_notes": " / ".join(notes),
        "evaluation_status": result.status,
        "evaluation_checks": [c.model_dump() for c in result.checks],
        "evaluation_count": count,
        "missing_requirements": list(result.missing_requirements),
        "validated_facts": dict(result.validated_facts or {}),
        "evaluation_user_message": result.user_message or "",
        "evaluation_retry_target": result.retry_target if result.status == "retry" else "none",
        "evaluation_retry_args": dict(result.retry_args or {}) if result.status == "retry" else {},
        "retry_count": retry + 1 if result.status == "retry" else retry,
        "current_step": "verifier",
    }
    if result.status == "retry":
        updates.update(_prepare_retry(state, result))
    return updates


def _prepare_retry(state: AgentState, result: EvaluationResult) -> dict:
    args = dict(result.retry_args or {})
    plan = dict(state.get("plan") or {})
    steps = [dict(step) for step in (plan.get("steps") or state.get("plan_steps") or [])]
    if args:
        for step in steps:
            if step.get("tool") == "analyze_excel":
                step["args"] = {**(step.get("args") or {}), **args}
        plan["steps"] = steps
    calls = []
    for step in steps:
        tool = step.get("tool")
        if not tool:
            continue
        call_args = dict(step.get("args") or {})
        if step.get("document_id"):
            call_args.setdefault("document_id", step["document_id"])
        calls.append({"tool": tool, "args": call_args})
    out = {
        "plan": plan,
        "plan_steps": steps,
        "evaluation_retry_args": args,
        "evaluation_retry_target": result.retry_target or "tools",
    }
    if calls:
        out["tool_calls"] = calls
    return out
