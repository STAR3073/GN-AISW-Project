from pathlib import Path

from app.agent.state import AgentState
from app.services.document_service import parse_document


def document_parser(state: AgentState) -> dict:
    document = dict(state.get("document") or {})
    raw = (document.get("raw_text") or "").strip()
    if not raw and document.get("stored_path"):
        parsed = parse_document(Path(document["stored_path"]), document.get("mime"))
        document["raw_text"] = parsed["text"]
        document["page_count"] = parsed["page_count"]
        document["file_type"] = parsed["file_type"]
    elif raw:
        document["file_type"] = document.get("file_type") or document.get("mime") or "unknown"
    return {
        "document": document,
        "current_step": "document_parser",
    }
