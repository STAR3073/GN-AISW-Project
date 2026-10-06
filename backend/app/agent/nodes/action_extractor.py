from app.agent.prompts import EXTRACT_SYSTEM
from app.agent.state import AgentState
from app.config import get_settings
from app.models import new_id
from app.schemas.agent import ActionExtraction
from app.services.llm_service import get_llm
from app.services.priority import apply_priority_rules


def action_extractor(state: AgentState) -> dict:
    document = state.get("document") or {}
    text = (document.get("raw_text") or "")[:24000]
    understanding = state.get("understanding") or {}
    llm = get_llm()
    settings = get_settings()
    result = llm.structured(
        [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"문서 이해 결과:\n{understanding}\n\n"
                    f"원문:\n{text}"
                ),
            },
        ],
        ActionExtraction,
        model=settings.llm_extract_model,
    )
    actions = [item.model_dump() for item in result.actions]
    for action in actions:
        action["id"] = new_id()
        action["status"] = action.get("status") or "TODO"
    actions = apply_priority_rules(actions)
    return {
        "actions": actions,
        "current_step": "action_extractor",
    }
