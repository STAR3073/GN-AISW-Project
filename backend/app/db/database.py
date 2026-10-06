from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine():
    settings = get_settings()
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        echo=False,
    )


engine = _engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _table_columns(conn, table: str) -> dict:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1]: row for row in rows}


def _add_column(conn, table: str, column: str, ddl: str) -> None:
    cols = _table_columns(conn, table)
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def _migrate_schema() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        if "documents" in tables:
            _add_column(conn, "documents", "project_id", "project_id VARCHAR(36)")
            _add_column(conn, "documents", "size_bytes", "size_bytes INTEGER DEFAULT 0")
        if "sessions" in tables:
            _add_column(conn, "sessions", "project_id", "project_id VARCHAR(36)")
            _add_column(conn, "sessions", "title", "title VARCHAR(128) DEFAULT '새 대화'")
            _add_column(conn, "sessions", "title_source", "title_source VARCHAR(16) DEFAULT 'default'")
            cols = _table_columns(conn, "sessions")
            doc_col = cols.get("document_id")
            if doc_col is not None and int(doc_col[3] or 0) == 1:
                conn.execute(
                    text(
                        """
                        CREATE TABLE sessions_new (
                            id VARCHAR(36) PRIMARY KEY,
                            project_id VARCHAR(36),
                            document_id VARCHAR(36),
                            title VARCHAR(128) DEFAULT '새 대화',
                            title_source VARCHAR(16) DEFAULT 'default',
                            state_json JSON,
                            created_at DATETIME,
                            updated_at DATETIME
                        )
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO sessions_new (id, project_id, document_id, title, title_source, state_json, created_at, updated_at)
                        SELECT id, project_id, document_id, COALESCE(NULLIF(title, ''), '새 대화'),
                               COALESCE(NULLIF(title_source, ''), 'default'),
                               state_json, created_at, updated_at
                        FROM sessions
                        """
                    )
                )
                conn.execute(text("DROP TABLE sessions"))
                conn.execute(text("ALTER TABLE sessions_new RENAME TO sessions"))
        conn.execute(text("PRAGMA foreign_keys=ON"))


def _seed_default_project() -> None:
    from app.models import AgentSession, ChatDocument, Document, Project, utcnow

    db = SessionLocal()
    try:
        project = db.query(Project).order_by(Project.created_at.asc()).first()
        if not project:
            project = Project(name="\uacbd\ub0a8 AI \uacbd\uc9c4\ub300\ud68c")
            db.add(project)
            db.flush()
        for doc in db.query(Document).filter(Document.project_id.is_(None)).all():
            doc.project_id = project.id
            if not doc.size_bytes and doc.stored_path:
                path = Path(doc.stored_path)
                if path.exists():
                    doc.size_bytes = path.stat().st_size
        for session in db.query(AgentSession).filter(AgentSession.project_id.is_(None)).all():
            session.project_id = project.id
            if not (session.title or "").strip():
                session.title = "새 대화"
            if session.document_id:
                exists = (
                    db.query(ChatDocument)
                    .filter_by(session_id=session.id, document_id=session.document_id)
                    .first()
                )
                if not exists:
                    db.add(
                        ChatDocument(
                            session_id=session.id,
                            document_id=session.document_id,
                            created_at=utcnow(),
                        )
                    )
        for session in db.query(AgentSession).all():
            source = (session.title_source or "").strip() or "default"
            titled = (session.title or "").strip() not in {"", "새 대화"}
            if source == "default" and titled:
                session.title_source = "auto"
            elif not source:
                session.title_source = "auto" if titled else "default"
        db.commit()
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_schema()
    Base.metadata.create_all(bind=engine)
    _seed_default_project()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
