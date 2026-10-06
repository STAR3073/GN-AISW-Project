from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    llm_extract_model: str = "gpt-4.1-mini"
    llm_plan_model: str = "gpt-4.1"
    llm_mock: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'docaction.db').as_posix()}"
    upload_dir: Path = BACKEND_DIR / "storage" / "uploads"
    draft_dir: Path = BACKEND_DIR / "storage" / "drafts"
    convert_dir: Path = BACKEND_DIR / "storage" / "converted"
    frontend_dir: Path = PROJECT_DIR / "frontend"
    max_upload_bytes: int = 20 * 1024 * 1024
    soffice_path: str = r"C:\Program Files\LibreOffice\program\soffice.com"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.draft_dir.mkdir(parents=True, exist_ok=True)
    settings.convert_dir.mkdir(parents=True, exist_ok=True)
    (BACKEND_DIR / "data").mkdir(parents=True, exist_ok=True)
    return settings
