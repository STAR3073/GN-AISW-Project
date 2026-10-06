from pathlib import Path

from app.config import get_settings
from app.services.llm_service import get_llm


def generate_draft(
    session_id: str,
    title: str,
    document_summary: str,
    actions: list[dict],
    extra_context: str = "",
) -> dict:
    settings = get_settings()
    safe_name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in title)[:80]
    path = Path(settings.draft_dir) / f"{session_id[:8]}_{safe_name}.md"

    action_lines = "\n".join(
        f"- {a.get('title')} ({a.get('priority')}, 마감 {a.get('deadline') or '-'})"
        for a in actions
    )
    llm = get_llm()
    body = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "당신은 경진대회 제출 문서를 쓰는 실무 작성자다. "
                    "마크다운으로 초안을 작성한다. 없는 사실을 단정하지 말고, "
                    "문서에서 확인된 Action을 중심으로 목차와 초안 본문을 채운다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"제목: {title}\n\n"
                    f"문서 요약:\n{document_summary}\n\n"
                    f"Action Items:\n{action_lines}\n\n"
                    f"{extra_context}"
                ),
            },
        ]
    )
    path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
    return {
        "title": title,
        "path": str(path),
        "preview": body[:500],
    }
