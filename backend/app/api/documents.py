from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import get_db
from app.models import Action, ChatDocument, Document, Project, Task
from app.schemas import DocumentOut
from app.services.document_service import parse_document

router = APIRouter()


def document_to_out(row: Document) -> DocumentOut:
    suffix = Path(row.filename or "").suffix.lower().lstrip(".")
    size = int(row.size_bytes or 0)
    if not size and row.stored_path:
        path = Path(row.stored_path)
        if path.exists():
            size = path.stat().st_size
    return DocumentOut(
        id=row.id,
        filename=row.filename,
        mime=row.mime,
        page_count=row.page_count,
        uploaded_at=row.uploaded_at,
        char_count=len(row.raw_text or ""),
        summary=row.summary_json,
        project_id=row.project_id,
        size_bytes=size,
        file_type=suffix.upper(),
    )


def resolve_project_id(db: Session, project_id: str | None) -> str | None:
    if project_id:
        if not db.get(Project, project_id):
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        return project_id
    row = db.query(Project).order_by(Project.created_at.asc()).first()
    return row.id if row else None


@router.post("", response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(...),
    project_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "파일명이 없습니다.")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".doc", ".txt", ".md", ".hwp", ".hwpx", ".xlsx", ".xls", ".csv"}:
        raise HTTPException(400, "PDF, DOCX, TXT, MD, HWP, XLSX, XLS, CSV만 지원합니다.")

    settings = get_settings()
    stored = settings.upload_dir / f"{uuid4().hex}{suffix}"
    content = await file.read()
    stored.write_bytes(content)

    try:
        parsed = parse_document(stored, file.content_type)
    except Exception as exc:
        raise HTTPException(400, f"문서를 읽지 못했습니다: {exc}") from exc

    if not (parsed.get("text") or "").strip():
        raise HTTPException(400, "문서에서 텍스트를 추출하지 못했습니다. 스캔본이면 OCR이 필요합니다.")

    doc = Document(
        filename=file.filename,
        mime=file.content_type or suffix,
        stored_path=str(stored),
        raw_text=parsed["text"],
        page_count=parsed.get("page_count") or 0,
        size_bytes=len(content),
        project_id=resolve_project_id(db, project_id),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return document_to_out(doc)


@router.get("", response_model=list[DocumentOut])
def list_documents(project_id: str | None = Query(default=None), db: Session = Depends(get_db)):
    query = db.query(Document)
    if project_id:
        query = query.filter(Document.project_id == project_id)
    rows = query.order_by(Document.uploaded_at.desc()).all()
    return [document_to_out(row) for row in rows]


@router.post("/from-attachment/{attachment_id}", response_model=DocumentOut)
def document_from_attachment(
    attachment_id: str,
    project_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    from app.models import Attachment

    att = db.get(Attachment, attachment_id)
    if not att:
        raise HTTPException(404, "첨부파일을 찾을 수 없습니다.")
    path = Path(att.stored_path)
    if not path.exists():
        raise HTTPException(404, "파일이 디스크에서 삭제되었습니다.")

    pid = resolve_project_id(db, project_id)
    existing = (
        db.query(Document)
        .filter(Document.stored_path == att.stored_path)
        .order_by(Document.uploaded_at.desc())
        .first()
    )
    if existing:
        if pid and not existing.project_id:
            existing.project_id = pid
            db.commit()
            db.refresh(existing)
        return document_to_out(existing)

    try:
        parsed = parse_document(path, att.mime)
    except Exception as exc:
        raise HTTPException(400, f"문서를 읽지 못했습니다: {exc}") from exc
    if not (parsed.get("text") or "").strip():
        raise HTTPException(400, "문서에서 텍스트를 추출하지 못했습니다.")

    doc = Document(
        filename=att.filename,
        mime=att.mime,
        stored_path=att.stored_path,
        raw_text=parsed["text"],
        page_count=parsed.get("page_count") or 0,
        size_bytes=att.size_bytes or path.stat().st_size,
        project_id=pid,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return document_to_out(doc)


@router.api_route("/{document_id}/file", methods=["GET", "HEAD"])
def download_document_file(
    document_id: str,
    download: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    row = db.get(Document, document_id)
    if not row:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    path = Path(row.stored_path)
    if not path.exists():
        raise HTTPException(404, "파일이 디스크에서 삭제되었습니다.")
    suffix = Path(row.filename).suffix.lower()
    media = {
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return FileResponse(
        path,
        media_type=media,
        filename=row.filename,
        content_disposition_type="attachment" if download else "inline",
    )


@router.delete("/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db)):
    row = db.get(Document, document_id)
    if not row:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    db.query(ChatDocument).filter(ChatDocument.document_id == document_id).delete()
    action_ids = [a.id for a in db.query(Action).filter(Action.document_id == document_id).all()]
    if action_ids:
        db.query(Task).filter(Task.action_id.in_(action_ids)).delete(synchronize_session=False)
        db.query(Action).filter(Action.id.in_(action_ids)).delete(synchronize_session=False)
    stored = row.stored_path
    db.delete(row)
    db.commit()
    if stored:
        path = Path(stored)
        leftover = db.query(Document).filter(Document.stored_path == stored).count()
        if leftover == 0 and path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    return {"ok": True, "id": document_id}


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db)):
    row = db.get(Document, document_id)
    if not row:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    return document_to_out(row)
