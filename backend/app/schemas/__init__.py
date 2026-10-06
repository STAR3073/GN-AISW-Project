from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    mime: str
    page_count: int
    uploaded_at: datetime
    char_count: int = 0
    summary: dict | None = None
    project_id: str | None = None
    size_bytes: int = 0
    file_type: str = ""


class ActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    description: str
    deadline: str | None
    deliverable: str
    priority: str
    status: str
    source: str


class ActionStatusPatch(BaseModel):
    status: str


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    action_id: str | None = None
    title: str
    due_date: str | None
    status: str
    order_index: int
    estimated_hours: float


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    document_id: str | None = None
    project_id: str | None = None
    title: str = "새 대화"
    title_source: str = "default"
    created_at: datetime
    updated_at: datetime | None = None
    document_ids: list[str] = []


class SessionCreate(BaseModel):
    document_id: str | None = None
    project_id: str | None = None
    document_ids: list[str] = []
    title: str = "새 대화"


class SessionPatch(BaseModel):
    title: str | None = None


class ChatIn(BaseModel):
    content: str
    document_ids: list[str] | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    document_count: int = 0
    session_count: int = 0


class ProjectCreate(BaseModel):
    name: str = "새 프로젝트"


class ProjectPatch(BaseModel):
    name: str | None = None


class ChatDocumentIn(BaseModel):
    document_id: str


class EventOut(BaseModel):
    id: str
    step: str
    label: str
    payload: dict
    created_at: datetime


class CalendarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    start_at: str
    end_at: str
    source: str


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    created_at: datetime


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    mime: str
    kind: str
    size_bytes: int
    source_id: str | None = None
    created_at: datetime
