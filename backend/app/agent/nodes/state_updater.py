from datetime import datetime, timezone

from app.agent.excel_reply import pending_from_state
from app.agent.state import AgentState
from app.db.database import SessionLocal
from app.models import Action, AgentEvent, AgentSession, Document, Task, new_id


def _snapshot(state: AgentState) -> dict:
    skip = {"document", "documents"}
    snap = {}
    for key, value in state.items():
        if key in skip:
            continue
        snap[key] = value
    document = state.get("document") or {}
    snap["document"] = {
        "id": document.get("id"),
        "filename": document.get("filename"),
        "mime": document.get("mime"),
        "stored_path": document.get("stored_path"),
        "page_count": document.get("page_count"),
        "file_type": document.get("file_type"),
    }
    return snap


def state_updater(state: AgentState) -> dict:
    session_id = state.get("session_id")
    pending = pending_from_state(state)
    if pending is None and (state.get("plan") or {}).get("needs_clarification"):
        pending = state.get("pending_clarification")
    working = dict(state)
    working["pending_clarification"] = pending
    db = SessionLocal()
    try:
        session = db.get(AgentSession, session_id)
        if not session:
            return {"current_step": "state_updater", "error": "session not found", "pending_clarification": pending}

        session.state_json = _snapshot(working)
        session.updated_at = datetime.now(timezone.utc)

        if state.get("understanding"):
            document = db.get(Document, session.document_id) if session.document_id else None
            if document:
                document.summary_json = state.get("understanding")

        primary_doc_id = session.document_id or (state.get("document") or {}).get("id")
        if state.get("intent") in {"ingest", "execute"} and primary_doc_id:
            existing = {a.title: a for a in session.actions}
            for item in state.get("actions") or []:
                row = existing.get(item.get("title"))
                if row is None:
                    row = Action(
                        id=item.get("id") or new_id(),
                        document_id=primary_doc_id,
                        session_id=session.id,
                        title=item.get("title") or "",
                        description=item.get("description") or "",
                        deadline=item.get("deadline"),
                        deliverable=item.get("deliverable") or "",
                        priority=item.get("priority") or "P2",
                        status=item.get("status") or "TODO",
                        source=item.get("source") or "",
                    )
                    db.add(row)
                    existing[row.title] = row
                else:
                    row.description = item.get("description") or row.description
                    row.deadline = item.get("deadline") or row.deadline
                    row.deliverable = item.get("deliverable") or row.deliverable
                    row.priority = item.get("priority") or row.priority
                    row.status = item.get("status") or row.status
                    row.source = item.get("source") or row.source

            db.flush()
            title_to_id = {a.title: a.id for a in session.actions}
            if state.get("intent") == "ingest":
                db.query(Task).filter(Task.session_id == session.id).delete()
                for item in state.get("tasks") or []:
                    db.add(
                        Task(
                            id=item.get("id") or new_id(),
                            action_id=item.get("action_id")
                            or title_to_id.get(item.get("action_title") or "")
                            or None,
                            session_id=session.id,
                            title=item.get("title") or "",
                            due_date=item.get("due_date"),
                            status=item.get("status") or "TODO",
                            order_index=item.get("order_index") or 0,
                            estimated_hours=item.get("estimated_hours") or 2.0,
                        )
                    )

        db.add(
            AgentEvent(
                session_id=session.id,
                step="state_updater",
                label="상태 저장",
                payload_json={
                    "intent": state.get("intent"),
                    "interpreted_goal": state.get("interpreted_goal"),
                    "intent_source": state.get("intent_source"),
                    "intent_confidence": state.get("intent_confidence"),
                    "suggested_capability": state.get("suggested_capability"),
                    "evaluation_status": state.get("evaluation_status"),
                    "evaluation_count": state.get("evaluation_count"),
                    "retry_count": state.get("retry_count"),
                    "missing_requirements": state.get("missing_requirements"),
                    "actions": len(state.get("actions") or []),
                    "tasks": len(state.get("tasks") or []),
                },
            )
        )
        db.commit()
    finally:
        db.close()
    return {"current_step": "state_updater", "pending_clarification": pending}
