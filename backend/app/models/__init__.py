from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), default="새 프로젝트")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    documents: Mapped[list["Document"]] = relationship(back_populates="project")
    sessions: Mapped[list["AgentSession"]] = relationship(back_populates="project")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(512))
    mime: Mapped[str] = mapped_column(String(128))
    stored_path: Mapped[str] = mapped_column(String(1024))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    page_count: Mapped[int] = mapped_column(default=0)
    size_bytes: Mapped[int] = mapped_column(default=0)
    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped["Project | None"] = relationship(back_populates="documents")
    sessions: Mapped[list["AgentSession"]] = relationship(back_populates="document")
    actions: Mapped[list["Action"]] = relationship(back_populates="document")
    chat_links: Mapped[list["ChatDocument"]] = relationship(back_populates="document")


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deliverable: Mapped[str] = mapped_column(String(512), default="")
    priority: Mapped[str] = mapped_column(String(8), default="P2")
    status: Mapped[str] = mapped_column(String(32), default="TODO")
    source: Mapped[str] = mapped_column(Text, default="")

    document: Mapped["Document"] = relationship(back_populates="actions")
    session: Mapped["AgentSession"] = relationship(back_populates="actions")
    tasks: Mapped[list["Task"]] = relationship(back_populates="action", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action_id: Mapped[str | None] = mapped_column(ForeignKey("actions.id"), nullable=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    title: Mapped[str] = mapped_column(String(512))
    due_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="TODO")
    order_index: Mapped[int] = mapped_column(default=0)
    estimated_hours: Mapped[float] = mapped_column(default=2.0)

    action: Mapped["Action"] = relationship(back_populates="tasks")


class AgentSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(128), default="새 대화")
    title_source: Mapped[str] = mapped_column(String(16), default="default")
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    project: Mapped["Project | None"] = relationship(back_populates="sessions")
    document: Mapped["Document | None"] = relationship(back_populates="sessions")
    actions: Mapped[list["Action"]] = relationship(back_populates="session")
    messages: Mapped[list["Message"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    events: Mapped[list["AgentEvent"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    calendar_events: Mapped[list["CalendarEvent"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    document_links: Mapped[list["ChatDocument"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatDocument(Base):
    __tablename__ = "chat_documents"
    __table_args__ = (UniqueConstraint("session_id", "document_id", name="uq_chat_document"),)

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped["AgentSession"] = relationship(back_populates="document_links")
    document: Mapped["Document"] = relationship(back_populates="chat_links")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped["AgentSession"] = relationship(back_populates="messages")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    step: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128), default="")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped["AgentSession"] = relationship(back_populates="events")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    title: Mapped[str] = mapped_column(String(512))
    start_at: Mapped[str] = mapped_column(String(32))
    end_at: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32), default="mock")

    session: Mapped["AgentSession"] = relationship(back_populates="calendar_events")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(512))
    mime: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(16), default="other")
    stored_path: Mapped[str] = mapped_column(String(1024))
    size_bytes: Mapped[int] = mapped_column(default=0)
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
