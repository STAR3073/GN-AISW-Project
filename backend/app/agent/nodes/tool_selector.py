from app.agent.excel_reply import excel_payload_from_state
from app.agent.prompts import SELECT_SYSTEM
from app.agent.state import AgentState
from app.config import get_settings
from app.schemas.agent import ToolSelection
from app.services.llm_service import get_llm
from app.tools.registry import is_registered


def _calls_from_plan(state: AgentState) -> list[dict]:
    plan = state.get("plan") or {}
    calls: list[dict] = []
    for step in plan.get("steps") or []:
        tool = step.get("tool")
        if not tool or not is_registered(str(tool)):
            continue
        args = dict(step.get("args") or {})
        if step.get("document_id"):
            args.setdefault("document_id", step["document_id"])
        calls.append({"tool": tool, "args": args})
    return calls


def _done_tools(state: AgentState) -> set[str]:
    done: set[str] = set()
    for row in state.get("execution_results") or []:
        if not row.get("ok"):
            continue
        tool = row.get("tool")
        if tool:
            done.add(str(tool))
    excel = excel_payload_from_state(state)
    if excel and excel.get("needs_clarification"):
        done.discard("analyze_excel")
        done.discard("generate_report")
    if state.get("evaluation_status") == "retry" and (state.get("evaluation_retry_target") or "tools") == "tools":
        done.discard("analyze_excel")
    return done


def _next_plan_calls(state: AgentState) -> list[dict]:
    done = _done_tools(state)
    pending: list[dict] = []
    for call in _calls_from_plan(state):
        if call.get("tool") in done:
            continue
        pending.append(call)
        break
    return pending


def _fallback_calls(state: AgentState) -> list[dict]:
    message = state.get("user_message") or ""
    if "초안" in message or "작성해" in message:
        title = "완료보고서 초안"
        for action in state.get("actions") or []:
            if "보고서" in action.get("title", "") or "설명서" in action.get("title", ""):
                title = f"{action['title']} 초안"
                break
        return [{"tool": "generate_draft", "args": {"title": title}}]
    return [
        {"tool": "create_tasks", "args": {}},
        {"tool": "create_calendar_events", "args": {}},
    ]


def tool_selector(state: AgentState) -> dict:
    if state.get("intent") == "ingest":
        return {
            "tool_calls": [
                {"tool": "create_tasks", "args": {}},
                {"tool": "create_calendar_events", "args": {}},
            ],
            "current_step": "tool_selector",
        }
    planned = _next_plan_calls(state)
    if planned:
        retry_args = state.get("evaluation_retry_args") or {}
        if retry_args:
            for call in planned:
                if call.get("tool") == "analyze_excel":
                    call["args"] = {**(call.get("args") or {}), **dict(retry_args)}
        return {"tool_calls": planned, "current_step": "tool_selector"}
    llm = get_llm()
    settings = get_settings()
    try:
        selection = llm.structured(
            [
                {"role": "system", "content": SELECT_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"intent={state.get('intent')}\n"
                        f"message={state.get('user_message')}\n"
                        f"plan={state.get('plan')}\n"
                        f"actions={[a.get('title') for a in (state.get('actions') or [])]}"
                    ),
                },
            ],
            ToolSelection,
            model=settings.llm_extract_model,
        )
        calls = [c.model_dump() for c in selection.tool_calls] or _fallback_calls(state)
    except Exception:
        calls = _fallback_calls(state)
    return {"tool_calls": calls, "current_step": "tool_selector"}
