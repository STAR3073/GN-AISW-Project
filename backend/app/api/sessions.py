import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent.graph import get_graph
from app.agent.nodes.responder import build_reply_messages, fallback_reply, persist_turn
from app.agent.state import empty_state
from app.config import get_settings
from app.db.database import SessionLocal, get_db
from app.services.chat_title import generate_session_title
from app.services.llm_service import MockLLMProvider, get_llm
from app.models import (
    Action,
    AgentEvent,
    AgentSession,
    CalendarEvent,
    ChatDocument,
    Document,
    Message,
    Task,
)
from app.schemas import (
    ActionOut,
    CalendarOut,
    ChatDocumentIn,
    ChatIn,
    DocumentOut,
    EventOut,
    MessageOut,
    SessionCreate,
    SessionOut,
    SessionPatch,
    TaskOut,
)
from app.api.documents import document_to_out, resolve_project_id
from app.api.projects import session_to_out

router = APIRouter()

STEP_META = {
    "intent_router": ("step", "의도 파악"),
    "document_parser": ("step", "문서 분석 중"),
    "document_understanding": ("understanding", "요구사항 추출"),
    "action_extractor": ("actions", "Action Item 생성"),
    "task_planner": ("plan", "업무 계획 생성"),
    "planner": ("step", "업무 계획 수립"),
    "tool_selector": ("tool", "Tool 선택"),
    "tool_executor": ("tool", "Tool 실행"),
    "verifier": ("step", "결과 검증"),
    "state_updater": ("status", "상태 저장"),
    "responder": ("message", "응답 작성"),
}


def _session_or_404(db: Session, session_id: str) -> AgentSession:
    row = db.get(AgentSession, session_id)
    if not row:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    return row


def _link_documents(db: Session, session: AgentSession, document_ids: list[str]) -> None:
    seen: set[str] = set()
    for document_id in document_ids:
        if not document_id or document_id in seen:
            continue
        seen.add(document_id)
        doc = db.get(Document, document_id)
        if not doc:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        exists = (
            db.query(ChatDocument)
            .filter_by(session_id=session.id, document_id=document_id)
            .first()
        )
        if not exists:
            db.add(ChatDocument(session_id=session.id, document_id=document_id))
        if not session.document_id:
            session.document_id = document_id


def _incoming_document_ids(document_ids: list[str] | None) -> list[str]:
    if not document_ids:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in document_ids:
        document_id = str(raw or "").strip()
        if not document_id or document_id in seen:
            continue
        seen.add(document_id)
        out.append(document_id)
    return out


def _replace_documents(db: Session, session: AgentSession, document_ids: list[str]) -> None:
    ids = _incoming_document_ids(document_ids)
    if not ids:
        return
    session.document_links.clear()
    db.flush()
    session.document_id = None
    _link_documents(db, session, ids)


def _session_documents(db: Session, session: AgentSession) -> list[Document]:
    links = (
        db.query(ChatDocument)
        .filter(ChatDocument.session_id == session.id)
        .order_by(ChatDocument.created_at.asc())
        .all()
    )
    docs: list[Document] = []
    for link in links:
        row = db.get(Document, link.document_id)
        if row:
            docs.append(row)
    if not docs and session.document_id:
        row = db.get(Document, session.document_id)
        if row:
            docs.append(row)
    return docs


def _combine_documents(docs: list[Document]) -> dict:
    if not docs:
        return {
            "id": "",
            "filename": "",
            "mime": "",
            "stored_path": "",
            "raw_text": "",
            "page_count": 0,
            "file_type": "",
        }
    blobs = [f"===== 문서: {row.filename} =====\n{row.raw_text or ''}" for row in docs]
    first = docs[0]
    return {
        "id": first.id,
        "filename": " / ".join(row.filename for row in docs),
        "mime": first.mime,
        "stored_path": first.stored_path,
        "raw_text": "\n\n".join(blobs),
        "page_count": sum(row.page_count or 0 for row in docs),
        "file_type": first.mime,
    }


@router.post("", response_model=SessionOut)
def create_session(body: SessionCreate, db: Session = Depends(get_db)):
    document_ids = list(body.document_ids or [])
    if body.document_id:
        document_ids.insert(0, body.document_id)
    project_id = body.project_id
    if not project_id and document_ids:
        first_doc = db.get(Document, document_ids[0])
        if first_doc:
            project_id = first_doc.project_id
    project_id = resolve_project_id(db, project_id)
    if not project_id:
        raise HTTPException(400, "프로젝트가 없습니다.")
    session = AgentSession(
        project_id=project_id,
        document_id=document_ids[0] if document_ids else None,
        title=(body.title or "").strip() or "새 대화",
        title_source="default",
        state_json={},
    )
    db.add(session)
    db.flush()
    if document_ids:
        _link_documents(db, session, document_ids)
    db.commit()
    db.refresh(session)
    return session_to_out(session, db)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)):
    return session_to_out(_session_or_404(db, session_id), db)


@router.patch("/{session_id}", response_model=SessionOut)
def patch_session(session_id: str, body: SessionPatch, db: Session = Depends(get_db)):
    session = _session_or_404(db, session_id)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "제목이 비어 있습니다.")
        session.title = title[:128]
        session.title_source = "user"
        session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session_to_out(session, db)


@router.delete("/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)):
    session = _session_or_404(db, session_id)
    db.query(ChatDocument).filter(ChatDocument.session_id == session_id).delete()
    db.query(Message).filter(Message.session_id == session_id).delete()
    db.query(AgentEvent).filter(AgentEvent.session_id == session_id).delete()
    db.query(CalendarEvent).filter(CalendarEvent.session_id == session_id).delete()
    action_ids = [a.id for a in db.query(Action).filter(Action.session_id == session_id).all()]
    if action_ids:
        db.query(Task).filter(Task.action_id.in_(action_ids)).delete(synchronize_session=False)
    db.query(Action).filter(Action.session_id == session_id).delete()
    db.query(Task).filter(Task.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"ok": True, "id": session_id}


@router.get("/{session_id}/documents", response_model=list[DocumentOut])
def list_session_documents(session_id: str, db: Session = Depends(get_db)):
    session = _session_or_404(db, session_id)
    return [document_to_out(row) for row in _session_documents(db, session)]


@router.post("/{session_id}/documents", response_model=SessionOut)
def add_session_document(session_id: str, body: ChatDocumentIn, db: Session = Depends(get_db)):
    session = _session_or_404(db, session_id)
    _link_documents(db, session, [body.document_id])
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session_to_out(session, db)


@router.delete("/{session_id}/documents/{document_id}", response_model=SessionOut)
def remove_session_document(session_id: str, document_id: str, db: Session = Depends(get_db)):
    session = _session_or_404(db, session_id)
    db.query(ChatDocument).filter_by(session_id=session_id, document_id=document_id).delete()
    db.flush()
    if session.document_id == document_id:
        rest = _session_documents(db, session)
        session.document_id = rest[0].id if rest else None
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session_to_out(session, db)


@router.get("/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: str, db: Session = Depends(get_db)):
    _session_or_404(db, session_id)
    rows = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [MessageOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{session_id}/actions", response_model=list[ActionOut])
def list_actions(session_id: str, db: Session = Depends(get_db)):
    _session_or_404(db, session_id)
    rows = db.query(Action).filter(Action.session_id == session_id).all()
    order = {"P1": 0, "P2": 1, "P3": 2}
    rows.sort(key=lambda r: (order.get(r.priority, 9), r.deadline or "9999", r.title))
    return [ActionOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{session_id}/tasks", response_model=list[TaskOut])
def list_tasks(session_id: str, db: Session = Depends(get_db)):
    _session_or_404(db, session_id)
    rows = (
        db.query(Task)
        .filter(Task.session_id == session_id)
        .order_by(Task.order_index.asc())
        .all()
    )
    return [TaskOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{session_id}/events", response_model=list[EventOut])
def list_events(session_id: str, db: Session = Depends(get_db)):
    _session_or_404(db, session_id)
    rows = (
        db.query(AgentEvent)
        .filter(AgentEvent.session_id == session_id)
        .order_by(AgentEvent.created_at.asc())
        .all()
    )
    return [
        EventOut(
            id=r.id,
            step=r.step,
            label=r.label,
            payload=r.payload_json or {},
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/{session_id}/calendar", response_model=list[CalendarOut])
def list_calendar(session_id: str, db: Session = Depends(get_db)):
    _session_or_404(db, session_id)
    rows = (
        db.query(CalendarEvent)
        .filter(CalendarEvent.session_id == session_id)
        .order_by(CalendarEvent.start_at.asc())
        .all()
    )
    return [CalendarOut.model_validate(r, from_attributes=True) for r in rows]


def _build_initial_state(session: AgentSession, documents: list[Document], content: str) -> dict:
    saved = session.state_json or {}
    state = empty_state()
    persist_keys = {
        "document_summary",
        "requirements",
        "actions",
        "tasks",
        "conversation_history",
        "understanding",
        "plan",
        "plan_steps",
        "pending_clarification",
        "document_column_mappings",
        "analysis_filter",
        "interpreted_goal",
        "suggested_capability",
        "intent_source",
    }
    state.update({k: v for k, v in saved.items() if k in persist_keys})
    state["session_id"] = session.id
    state["user_message"] = content
    state["current_step"] = ""
    state["assistant_reply"] = ""
    state["tool_calls"] = []
    state["execution_results"] = []
    state["retry_count"] = 0
    state["verification_ok"] = True
    state["evaluation_count"] = 0
    state["evaluation_status"] = ""
    state["evaluation_checks"] = []
    state["missing_requirements"] = []
    state["validated_facts"] = {}
    state["evaluation_retry_args"] = {}
    state["evaluation_retry_target"] = ""
    state["evaluation_user_message"] = ""
    state["error"] = ""
    state["document"] = _combine_documents(documents)
    state["documents"] = [
        {
            "id": row.id,
            "filename": row.filename,
            "mime": row.mime,
            "stored_path": row.stored_path,
            "raw_text": row.raw_text or "",
            "page_count": row.page_count,
        }
        for row in documents
    ]
    if session.actions and not state.get("actions"):
        state["actions"] = [
            {
                "id": a.id,
                "title": a.title,
                "description": a.description,
                "deadline": a.deadline,
                "deliverable": a.deliverable,
                "priority": a.priority,
                "status": a.status,
                "source": a.source,
            }
            for a in session.actions
        ]
    if session.messages:
        state["conversation_history"] = [
            {"role": m.role, "content": m.content} for m in session.messages
        ]
    return state


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _iter_reply_tokens(state: dict):
    llm = get_llm()
    messages = build_reply_messages(state)
    settings = get_settings()
    delay = 0.02 if isinstance(llm, MockLLMProvider) else 0
    try:
        for token in llm.stream_chat(messages, model=settings.llm_plan_model):
            if not token:
                continue
            yield token
            await asyncio.sleep(delay or 0)
    except Exception:
        text = fallback_reply(state)
        for i in range(0, len(text), 2):
            yield text[i : i + 2]
            await asyncio.sleep(0.02)


def _log_event(session_id: str, step: str, label: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        db.add(
            AgentEvent(
                session_id=session_id,
                step=step,
                label=label,
                payload_json=payload,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()


@router.post("/{session_id}/messages")
async def post_message(session_id: str, body: ChatIn, db: Session = Depends(get_db)):
    session = _session_or_404(db, session_id)
    if not (body.content or "").strip():
        raise HTTPException(400, "메시지가 비어 있습니다.")
    incoming_ids = _incoming_document_ids(body.document_ids)
    if incoming_ids:
        _replace_documents(db, session, incoming_ids)
        db.commit()
        db.refresh(session)

    documents = _session_documents(db, session)
    initial = _build_initial_state(session, documents, body.content.strip())
    graph = get_graph()

    async def generate():
        prev = None
        last = initial
        try:
            async for values in graph.astream(initial, stream_mode="values"):
                last = values
                step = values.get("current_step")
                if not step or step == prev:
                    continue
                prev = step
                event_name, label = STEP_META.get(step, ("step", step))
                payload = {"node": step, "label": label, "intent": values.get("intent")}
                if step == "document_understanding":
                    payload["understanding"] = values.get("understanding")
                    event_name = "understanding"
                elif step == "action_extractor":
                    payload["actions"] = values.get("actions")
                    event_name = "actions"
                elif step == "task_planner":
                    payload["tasks"] = values.get("tasks")
                    event_name = "plan"
                elif step in {"tool_selector", "tool_executor"}:
                    payload["tool_calls"] = values.get("tool_calls")
                    payload["execution_results"] = values.get("execution_results")
                    event_name = "tool"
                    tools = [c.get("tool") for c in (values.get("tool_calls") or [])]
                    if step == "tool_executor" and "generate_report" in tools and "analyze_excel" not in tools:
                        label = "보고서 생성"
                    elif "analyze_excel" in tools:
                        label = "데이터 분석"
                    payload["label"] = label
                elif step == "responder":
                    event_name = "status"
                _log_event(session_id, step, label, {k: v for k, v in payload.items() if k != "understanding"})
                yield _sse(event_name, payload)

            yield _sse("status", {"node": "responder", "label": "답변 작성 중"})
            chunks: list[str] = []
            async for token in _iter_reply_tokens(last):
                chunks.append(token)
                yield _sse("token", {"content": token})
            reply = "".join(chunks).strip() or fallback_reply(last)
            persist_turn(session_id, body.content.strip(), reply)
            _log_event(session_id, "responder", "응답 완료", {"chars": len(reply)})
            done = {
                "session_id": session_id,
                "content": reply,
            }
            for row in reversed(list(last.get("execution_results") or [])):
                if row.get("tool") != "generate_report" or not row.get("ok"):
                    continue
                data = row.get("data") if isinstance(row.get("data"), dict) else {}
                if data.get("attachment_id"):
                    done["attachment"] = {
                        "id": data.get("attachment_id"),
                        "filename": data.get("filename") or "주간 품질 현황 보고서.docx",
                    }
                break
            yield _sse("done", done)
            generated = await asyncio.to_thread(generate_session_title, session_id)
            if generated:
                yield _sse(
                    "title",
                    {
                        "session_id": session_id,
                        "title": generated,
                        "title_source": "auto",
                    },
                )
        except Exception as exc:
            _log_event(session_id, "error", "실행 실패", {"detail": str(exc)})
            yield _sse("error", {"detail": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
