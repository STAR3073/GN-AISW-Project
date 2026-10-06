from app.agent.prompts import PLAN_SYSTEM
from app.agent.state import AgentState
from app.config import get_settings
from app.schemas.agent import TaskPlan
from app.services.llm_service import get_llm
from app.services.schedule import backschedule, infer_final_deadline
from app.tools.task_tool import materialize_tasks


def task_planner(state: AgentState) -> dict:
    actions = state.get("actions") or []
    understanding = state.get("understanding") or {}
    llm = get_llm()
    settings = get_settings()
    plan = llm.structured(
        [
            {"role": "system", "content": PLAN_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"문서 일정: {understanding.get('key_dates')}\n"
                    f"Actions: {actions}"
                ),
            },
        ],
        TaskPlan,
        model=settings.llm_plan_model,
    )
    planned = [t.model_dump() for t in plan.tasks]
    final = infer_final_deadline(actions)
    if plan.final_deadline:
        from datetime import datetime

        try:
            final = datetime.strptime(plan.final_deadline[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    planned = backschedule(planned, final)
    tasks = materialize_tasks(state.get("session_id") or "", actions, planned)
    return {
        "tasks": tasks,
        "current_step": "task_planner",
    }
