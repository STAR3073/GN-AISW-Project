from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models import (
    Action,
    AgentEvent,
    AgentSession,
    CalendarEvent,
    ChatDocument,
    Document,
    Message,
    Project,
    Task,
    utcnow,
)
from app.schemas import ProjectCreate, ProjectOut, ProjectPatch, SessionOut
from app.api.documents import document_to_out
from app.schemas import DocumentOut

router = APIRouter()


def _project_or_404(db: Session, project_id: str) -> Project:
    row = db.get(Project, project_id)
    if not row:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return row


def _to_out(row: Project, db: Session) -> ProjectOut:
    return ProjectOut(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        document_count=db.query(Document).filter(Document.project_id == row.id).count(),
        session_count=db.query(AgentSession).filter(AgentSession.project_id == row.id).count(),
    )


def session_to_out(row: AgentSession, db: Session) -> SessionOut:
    links = (
        db.query(ChatDocument.document_id)
        .filter(ChatDocument.session_id == row.id)
        .all()
    )
    ids = [item[0] for item in links]
    return SessionOut(
        id=row.id,
        document_id=row.document_id or (ids[0] if ids else None),
        project_id=row.project_id,
        title=row.title or "새 대화",
        title_source=row.title_source or "default",
        created_at=row.created_at,
        updated_at=row.updated_at,
        document_ids=ids,
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    rows = db.query(Project).order_by(Project.created_at.asc()).all()
    return [_to_out(row, db) for row in rows]


@router.post("", response_model=ProjectOut)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    name = (body.name or "").strip() or "새 프로젝트"
    row = Project(name=name)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row, db)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)):
    return _to_out(_project_or_404(db, project_id), db)


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(project_id: str, body: ProjectPatch, db: Session = Depends(get_db)):
    row = _project_or_404(db, project_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "프로젝트 이름이 비어 있습니다.")
        row.name = name
        row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return _to_out(row, db)


@router.delete("/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    row = _project_or_404(db, project_id)
    sessions = db.query(AgentSession).filter(AgentSession.project_id == project_id).all()
    session_ids = [s.id for s in sessions]
    if session_ids:
        db.query(ChatDocument).filter(ChatDocument.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(Message).filter(Message.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(AgentEvent).filter(AgentEvent.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(CalendarEvent).filter(CalendarEvent.session_id.in_(session_ids)).delete(synchronize_session=False)
        action_ids = [a.id for a in db.query(Action).filter(Action.session_id.in_(session_ids)).all()]
        if action_ids:
            db.query(Task).filter(Task.action_id.in_(action_ids)).delete(synchronize_session=False)
        db.query(Action).filter(Action.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.session_id.in_(session_ids)).delete(synchronize_session=False)
        db.query(AgentSession).filter(AgentSession.id.in_(session_ids)).delete(synchronize_session=False)
    docs = db.query(Document).filter(Document.project_id == project_id).all()
    doc_ids = [d.id for d in docs]
    if doc_ids:
        db.query(ChatDocument).filter(ChatDocument.document_id.in_(doc_ids)).delete(synchronize_session=False)
        action_ids = [a.id for a in db.query(Action).filter(Action.document_id.in_(doc_ids)).all()]
        if action_ids:
            db.query(Task).filter(Task.action_id.in_(action_ids)).delete(synchronize_session=False)
            db.query(Action).filter(Action.id.in_(action_ids)).delete(synchronize_session=False)
        db.query(Document).filter(Document.id.in_(doc_ids)).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True, "id": project_id}


@router.get("/{project_id}/sessions", response_model=list[SessionOut])
def list_project_sessions(project_id: str, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    rows = (
        db.query(AgentSession)
        .filter(AgentSession.project_id == project_id)
        .order_by(AgentSession.updated_at.desc(), AgentSession.created_at.desc())
        .all()
    )
    return [session_to_out(row, db) for row in rows]


@router.get("/{project_id}/documents", response_model=list[DocumentOut])
def list_project_documents(project_id: str, db: Session = Depends(get_db)):
    _project_or_404(db, project_id)
    rows = (
        db.query(Document)
        .filter(Document.project_id == project_id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return [document_to_out(row) for row in rows]
