import os
import subprocess
from pathlib import Path

from app.config import get_settings

HWP_SUFFIXES = {".hwp", ".hwpx"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".txt", ".md", ".hwp", ".hwpx", ".xlsx", ".xls", ".csv", *IMAGE_SUFFIXES}
KIND_BY_SUFFIX = {
    ".hwp": "hwp",
    ".hwpx": "hwp",
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".txt": "text",
    ".md": "text",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".csv": "spreadsheet",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".webp": "image",
}

_SOFFICE_CANDIDATES = (
    Path(r"C:\Program Files\LibreOffice\program\soffice.com"),
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.com"),
)


def kind_from_name(filename: str) -> str:
    return KIND_BY_SUFFIX.get(Path(filename).suffix.lower(), "other")


def is_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_SUFFIXES


def is_hwp(filename: str) -> bool:
    return Path(filename).suffix.lower() in HWP_SUFFIXES


def _file_uri(path: Path) -> str:
    posix = path.resolve().as_posix()
    if len(posix) >= 2 and posix[1] == ":":
        return "file:///" + posix
    return "file://" + posix


def find_soffice() -> Path:
    settings = get_settings()
    configured = Path(settings.soffice_path)
    for candidate in (configured, *_SOFFICE_CANDIDATES):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "HWP → PDF 변환기(LibreOffice)를 찾지 못했습니다. LibreOffice를 설치한 뒤 다시 시도하세요."
    )


def convert_to_pdf(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    soffice = find_soffice()
    profile = get_settings().convert_dir.parent / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(soffice),
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation={_file_uri(profile)}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(dest_dir),
        str(source),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=120,
        creationflags=flags,
        check=False,
    )
    expected = dest_dir / f"{source.stem}.pdf"
    if expected.exists() and expected.stat().st_size > 0:
        return expected
    detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="ignore").strip()
    raise RuntimeError(detail or "PDF 변환에 실패했습니다. HWP 파일이 손상되었거나 변환기가 지원하지 않습니다.")


def convert_to_pdf_unique(source: Path, dest_path: Path) -> Path:
    import shutil
    from uuid import uuid4

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.parent / f"_preview_{uuid4().hex}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        produced = convert_to_pdf(source, tmp)
        dest_path.write_bytes(produced.read_bytes())
        return dest_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def convert_to_text(source: Path, dest_dir: Path) -> str:
    dest_dir.mkdir(parents=True, exist_ok=True)
    soffice = find_soffice()
    profile = get_settings().convert_dir.parent / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(soffice),
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation={_file_uri(profile)}",
        "--convert-to",
        "txt:Text",
        "--outdir",
        str(dest_dir),
        str(source),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.run(
        cmd,
        capture_output=True,
        timeout=120,
        creationflags=flags,
        check=False,
    )
    expected = dest_dir / f"{source.stem}.txt"
    if expected.exists():
        return expected.read_text(encoding="utf-8", errors="ignore")
    return ""
