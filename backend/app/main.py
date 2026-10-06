from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import actions, attachments, documents, health, projects, sessions
from app.config import get_settings
from app.db.database import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    init_db()
    mode = "mock" if settings.llm_mock or not settings.openai_api_key else "openai"
    print(f"DocAction AI ready · llm={mode} · extract={settings.llm_extract_model} · plan={settings.llm_plan_model}")
    yield


settings = get_settings()
app = FastAPI(title="DocAction AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/health", tags=["health"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(attachments.router, prefix="/api/attachments", tags=["attachments"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(actions.router, prefix="/api/actions", tags=["actions"])


@app.get("/api")
def api_root():
    return {"name": "DocAction AI", "docs": "/docs"}


if settings.frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(settings.frontend_dir), html=True), name="ui")
