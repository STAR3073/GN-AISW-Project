import re

from app.agent.prompts import TITLE_SYSTEM
from app.config import get_settings
from app.db.database import SessionLocal
from app.models import AgentSession, Message, utcnow
from app.services.llm_service import get_llm

_BANNED = ("에 대한 대화", "사용자의 요청", "질문 및 답변", "새 대화")


def _context(messages: list[Message]) -> str:
    lines = []
    for row in messages[:4]:
        who = "사용자" if row.role == "user" else "AI"
        text = " ".join((row.content or "").split())[:400]
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines)


def _sanitize(raw: str) -> str:
    text = (raw or "").strip().splitlines()[0].strip()
    text = text.strip("\"'`“”‘’ ")
    text = re.sub(r"^제목\s*[:：]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if any(bad in text for bad in _BANNED):
        return ""
    if len(text) < 2:
        return ""
    if len(text) > 28:
        text = text[:28].rstrip()
    return text


def generate_session_title(session_id: str) -> str | None:
    db = SessionLocal()
    try:
        session = db.get(AgentSession, session_id)
        if not session:
            return None
        source = (session.title_source or "default").strip() or "default"
        if source == "user":
            return None
        rows = (
            db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        user_msgs = [m for m in rows if m.role == "user"]
        ai_msgs = [m for m in rows if m.role == "assistant"]
        if not user_msgs or not ai_msgs:
            return None
        if source == "auto":
            return None
        context = _context(rows)
        if not context:
            return None
        llm = get_llm()
        settings = get_settings()
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": TITLE_SYSTEM},
                    {"role": "user", "content": f"대화:\n{context}"},
                ],
                model=settings.llm_extract_model,
            )
        except Exception:
            raw = ""
        title = _sanitize(raw)
        if not title:
            return None
        session = db.get(AgentSession, session_id)
        if not session or (session.title_source or "default") == "user":
            return None
        session.title = title
        session.title_source = "auto"
        session.updated_at = utcnow()
        db.commit()
        return title
    finally:
        db.close()
