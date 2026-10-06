from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import get_db
from app.models import Attachment
from app.schemas import AttachmentOut
from app.services.hwp_converter import (
    convert_to_pdf,
    convert_to_pdf_unique,
    convert_to_text,
    is_allowed,
    is_hwp,
    kind_from_name,
)
from app.services.llm_service import get_llm

router = APIRouter()


def _to_out(row: Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=row.id,
        filename=row.filename,
        mime=row.mime,
        kind=row.kind,
        size_bytes=row.size_bytes,
        source_id=row.source_id,
        created_at=row.created_at,
    )


def _get_or_404(db: Session, attachment_id: str) -> Attachment:
    row = db.get(Attachment, attachment_id)
    if not row:
        raise HTTPException(404, "첨부파일을 찾을 수 없습니다.")
    return row


@router.post("", response_model=AttachmentOut)
async def upload_attachment(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename:
        raise HTTPException(400, "파일명이 없습니다.")
    if not is_allowed(file.filename):
        raise HTTPException(400, "지원하지 않는 파일 형식입니다. PDF, DOCX, TXT, MD, HWP, HWPX, XLSX, XLS, CSV, JPG, PNG만 가능합니다.")

    settings = get_settings()
    content = await file.read()
    if not content:
        raise HTTPException(400, "빈 파일은 업로드할 수 없습니다.")
    if len(content) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes / (1024 * 1024)
        raise HTTPException(400, f"파일 크기가 제한({limit_mb:.0f}MB)을 초과했습니다.")

    suffix = Path(file.filename).suffix.lower()
    stored = settings.upload_dir / f"{uuid4().hex}{suffix}"
    stored.write_bytes(content)

    row = Attachment(
        filename=file.filename,
        mime=file.content_type or suffix,
        kind=kind_from_name(file.filename),
        stored_path=str(stored),
        size_bytes=len(content),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.get("/{attachment_id}", response_model=AttachmentOut)
def get_attachment(attachment_id: str, db: Session = Depends(get_db)):
    return _to_out(_get_or_404(db, attachment_id))


@router.api_route("/{attachment_id}/file", methods=["GET", "HEAD"])
def download_attachment(
    attachment_id: str,
    download: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    row = _get_or_404(db, attachment_id)
    path = Path(row.stored_path)
    if not path.exists():
        raise HTTPException(404, "파일이 디스크에서 삭제되었습니다.")
    media = "application/pdf" if row.kind in {"pdf", "preview_pdf"} else None
    if media is None and row.kind == "image":
        suffix = Path(row.filename).suffix.lower()
        media = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")
    if media is None and row.kind == "text":
        media = "text/plain; charset=utf-8"
    if media is None:
        media = "application/octet-stream"
    return FileResponse(
        path,
        media_type=media,
        filename=row.filename,
        content_disposition_type="attachment" if download else "inline",
    )


@router.post("/{attachment_id}/convert-pdf", response_model=AttachmentOut)
def convert_attachment_pdf(attachment_id: str, db: Session = Depends(get_db)):
    source = _get_or_404(db, attachment_id)
    if not is_hwp(source.filename):
        raise HTTPException(400, "HWP 파일만 PDF로 변환할 수 있습니다.")

    existing = (
        db.query(Attachment)
        .filter(Attachment.source_id == source.id, Attachment.kind == "pdf")
        .order_by(Attachment.created_at.desc())
        .first()
    )
    if existing and Path(existing.stored_path).exists():
        return _to_out(existing)

    settings = get_settings()
    try:
        pdf_path = convert_to_pdf(Path(source.stored_path), settings.convert_dir)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"PDF 변환 실패: {exc}") from exc

    pdf_name = Path(source.filename).with_suffix(".pdf").name
    row = Attachment(
        filename=pdf_name,
        mime="application/pdf",
        kind="pdf",
        stored_path=str(pdf_path),
        size_bytes=pdf_path.stat().st_size,
        source_id=source.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.post("/{attachment_id}/preview-pdf", response_model=AttachmentOut)
def preview_attachment_pdf(attachment_id: str, db: Session = Depends(get_db)):
    source = _get_or_404(db, attachment_id)
    if not is_hwp(source.filename):
        raise HTTPException(400, "HWP 파일만 미리보기 PDF를 만들 수 있습니다.")

    existing = (
        db.query(Attachment)
        .filter(Attachment.source_id == source.id, Attachment.kind == "preview_pdf")
        .order_by(Attachment.created_at.desc())
        .first()
    )
    if existing and Path(existing.stored_path).exists():
        return _to_out(existing)

    settings = get_settings()
    dest = Path(settings.convert_dir) / f"preview_{uuid4().hex}.pdf"
    try:
        convert_to_pdf_unique(Path(source.stored_path), dest)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"미리보기 준비 실패: {exc}") from exc

    row = Attachment(
        filename=f"{Path(source.filename).stem}.preview.pdf",
        mime="application/pdf",
        kind="preview_pdf",
        stored_path=str(dest),
        size_bytes=dest.stat().st_size,
        source_id=source.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.get("/{attachment_id}/text")
def read_attachment_text(attachment_id: str, db: Session = Depends(get_db)):
    row = _get_or_404(db, attachment_id)
    path = Path(row.stored_path)
    if not path.exists():
        raise HTTPException(404, "파일이 디스크에서 삭제되었습니다.")
    settings = get_settings()
    try:
        if row.kind == "text":
            text = path.read_text(encoding="utf-8", errors="ignore")
        elif row.kind == "pdf":
            from app.services.document_service import parse_document

            text = parse_document(path, "application/pdf").get("text") or ""
        else:
            text = convert_to_text(path, settings.convert_dir)
    except Exception as exc:
        raise HTTPException(500, f"문서 내용을 읽지 못했습니다: {exc}") from exc
    if not (text or "").strip():
        raise HTTPException(400, "문서에서 텍스트를 추출하지 못했습니다.")
    return {"id": row.id, "filename": row.filename, "text": text[:12000]}


@router.post("/{attachment_id}/summarize")
def summarize_attachment(attachment_id: str, db: Session = Depends(get_db)):
    payload = read_attachment_text(attachment_id, db)
    llm = get_llm()
    summary = llm.chat(
        [
            {
                "role": "system",
                "content": "문서를 한국어로 짧게 요약한다. 핵심 목적, 일정, 제출물만 남긴다.",
            },
            {"role": "user", "content": payload["text"]},
        ]
    )
    return {"id": payload["id"], "filename": payload["filename"], "summary": summary}
