from langgraph.graph import END, START, StateGraph

from app.agent.nodes.action_extractor import action_extractor
from app.agent.nodes.document_parser import document_parser
from app.agent.nodes.document_understanding import document_understanding
from app.agent.nodes.intent_router import intent_router
from app.agent.nodes.planner import planner
from app.agent.nodes.responder import responder
from app.agent.nodes.state_updater import state_updater
from app.agent.nodes.task_planner import task_planner
from app.agent.nodes.tool_executor import tool_executor
from app.agent.nodes.tool_selector import tool_selector
from app.agent.nodes.verifier import verifier
from app.agent.router import after_plan, after_verify, route_from_state
from app.agent.state import AgentState

_graph = None


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("intent_router", intent_router)
    graph.add_node("document_parser", document_parser)
    graph.add_node("document_understanding", document_understanding)
    graph.add_node("action_extractor", action_extractor)
    graph.add_node("task_planner", task_planner)
    graph.add_node("planner", planner)
    graph.add_node("tool_selector", tool_selector)
    graph.add_node("tool_executor", tool_executor)
    graph.add_node("verifier", verifier)
    graph.add_node("state_updater", state_updater)
    graph.add_node("responder", responder)

    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges(
        "intent_router",
        route_from_state,
        {
            "ingest": "document_parser",
            "execute": "planner",
            "query": "responder",
        },
    )
    graph.add_edge("document_parser", "document_understanding")
    graph.add_edge("document_understanding", "action_extractor")
    graph.add_edge("action_extractor", "task_planner")
    graph.add_edge("task_planner", "tool_selector")
    graph.add_conditional_edges(
        "planner",
        after_plan,
        {
            "tools": "tool_selector",
            "reply": "state_updater",
        },
    )
    graph.add_edge("tool_selector", "tool_executor")
    graph.add_edge("tool_executor", "verifier")
    graph.add_conditional_edges(
        "verifier",
        after_verify,
        {
            "retry_plan": "planner",
            "retry_tools": "tool_selector",
            "continue_tools": "tool_selector",
            "ok": "state_updater",
        },
    )
    graph.add_edge("state_updater", "responder")
    graph.add_edge("responder", END)
    return graph.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
