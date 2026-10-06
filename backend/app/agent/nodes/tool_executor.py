from app.agent.state import AgentState
from app.tools.registry import UnknownToolError, invoke_tool


def tool_executor(state: AgentState) -> dict:
    results = list(state.get("execution_results") or [])
    working = dict(state)
    for call in state.get("tool_calls") or []:
        name = call.get("tool")
        args = call.get("args") or {}
        try:
            result = invoke_tool(name, args, working)
        except UnknownToolError as exc:
            results.append({"tool": name, "ok": False, "detail": str(exc)})
            continue
        entry: dict = {"tool": name, "ok": result.ok, "detail": result.detail}
        if result.data is not None:
            entry["data"] = result.data
        results.append(entry)
        if result.state_updates:
            working.update(result.state_updates)
    out = {
        "execution_results": results,
        "actions": list(working.get("actions") or []),
        "current_step": "tool_executor",
    }
    if "document_column_mappings" in working:
        out["document_column_mappings"] = working.get("document_column_mappings")
    return out
