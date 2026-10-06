from app.agent.prompts import UNDERSTAND_SYSTEM
from app.agent.state import AgentState
from app.config import get_settings
from app.schemas.agent import DocumentUnderstanding
from app.services.llm_service import get_llm


def document_understanding(state: AgentState) -> dict:
    document = state.get("document") or {}
    text = (document.get("raw_text") or "")[:24000]
    filename = document.get("filename") or ""
    llm = get_llm()
    settings = get_settings()
    result = llm.structured(
        [
            {"role": "system", "content": UNDERSTAND_SYSTEM},
            {
                "role": "user",
                "content": f"파일명: {filename}\n\n문서:\n{text}",
            },
        ],
        DocumentUnderstanding,
        model=settings.llm_extract_model,
    )
    payload = result.model_dump()
    return {
        "understanding": payload,
        "document_summary": result.summary,
        "requirements": result.requirements,
        "current_step": "document_understanding",
    }
