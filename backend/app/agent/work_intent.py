"""업무 실행 vs 일반 질문 판별. Intent Router와 Planner가 함께 사용한다."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Mapping

RuleVerdict = Literal["CONFIDENT_QUERY", "CONFIDENT_EXECUTE", "CONFIDENT_INGEST", "UNCERTAIN"]

TABULAR_SUFFIXES = {".xlsx", ".xls", ".csv"}

_TABULAR_MARK = re.compile(r"\[tabular file:[^\]]*\]", re.I)
_DOC_HEAD = re.compile(r"===== 문서:.*?=====")

CHITCHAT = ("안녕", "안녕하세요", "하이", "hello", "hi ", "ㅎㅇ", "고마워", "감사", "ㅋㅋ", "ㅎㅎ")
DEFINITION_HINTS = ("무슨 뜻", "의미", "정의", "이란 무엇", "라는 게")
STRONG_ANALYSIS = (
    "불량 현황",
    "품질 현황",
    "품목별",
    "공정별",
    "설비별",
    "이 엑셀",
    "엑셀 분석",
    "지난주",
    "안 좋아",
    "제일 문제",
    "뭐가 문제",
    "불량률",
)
ANALYSIS_VERBS = ("분석", "알려줘", "보여줘", "봐줘", "찾아줘", "비교", "문제", "안 좋아", "현황", "정리해")
QUALITY_TERMS = ("불량", "품질", "품목", "공정", "설비", "lot", "생산", "엑셀", "excel", "csv", "데이터")

QUERY_HINTS = ("아직", "남은 일", "해야 할 일이 뭐", "진행 상황", "상태 알려", "뭐가 남")
EXECUTE_HINTS = ("초안", "만들어줘", "작성해", "캘린더", "일정 등록", "생성해줘", "드래프트")
INGEST_HINTS = (
    "정리하고 진행",
    "해야 할 일 정리",
    "자료 정리",
    "문서 정리",
    "정리해",
    "확인하고",
    "진행해줘",
    "추출해",
    "해야할 일",
)
REINGEST_HINTS = ("자료 정리", "문서 정리", "다시 분석", "다시 정리", "처음부터")

_CHOICE_TAILS = (
    "기준으로해서",
    "기준으로해줘",
    "기준으로",
    "기준해서",
    "기준",
    "으로해줘",
    "로해줘",
    "해주세요",
    "해줘",
    "선택해줘",
    "선택",
    "걸로",
    "거로",
)
_CHOICE_PARTICLE = re.compile(r"(으로|로서|로써|를|을|이는|는|은|이|가|와|과|도|만|의)$")
_CHOICE_NUMBER = re.compile(
    r"(\d+)(번째걸로|번째거|번째로|번째|번으로|번거|번)?(으로해줘|선택해줘|으로|로|요|해줘|선택)?"
)
_CHOICE_ORDINALS = (
    ("첫번째걸로", 1),
    ("첫번째거", 1),
    ("첫번째", 1),
    ("첫째걸로", 1),
    ("첫째거", 1),
    ("첫째", 1),
    ("두번째걸로", 2),
    ("두번째거", 2),
    ("두번째", 2),
    ("둘째걸로", 2),
    ("둘째거", 2),
    ("둘째", 2),
    ("세번째걸로", 3),
    ("세번째거", 3),
    ("세번째", 3),
    ("셋째걸로", 3),
    ("셋째거", 3),
    ("셋째", 3),
)
_AMBIGUOUS_CHOICE = ("둘다", "모두", "전부", "아무거나", "상관없")


def iter_documents(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(state.get("documents") or []):
        if isinstance(item, dict):
            key = str(item.get("id") or item.get("stored_path") or item.get("filename") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            rows.append(item)
    doc = state.get("document")
    if isinstance(doc, dict) and (doc.get("id") or doc.get("filename")):
        key = str(doc.get("id") or doc.get("stored_path") or doc.get("filename") or "")
        if key not in seen:
            rows.append(doc)
    return rows


def is_tabular_filename(name: str | None) -> bool:
    text = str(name or "").strip().strip("\"'")
    if not text:
        return False
    return Path(text).suffix.lower() in TABULAR_SUFFIXES


def is_tabular_document(row: Mapping[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    if is_tabular_filename(row.get("filename")):
        return True
    stored = str(row.get("stored_path") or "").strip()
    if stored and is_tabular_filename(Path(stored).name):
        return True
    mime = str(row.get("mime") or "").lower()
    if any(token in mime for token in ("spreadsheetml", "ms-excel", "text/csv", "application/csv")):
        return True
    raw = str(row.get("raw_text") or "")
    return bool(_TABULAR_MARK.search(raw))


def tabular_documents(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [row for row in iter_documents(state) if is_tabular_document(row)]


def strip_tabular_stubs(text: str) -> str:
    cleaned = _DOC_HEAD.sub("", text or "")
    cleaned = _TABULAR_MARK.sub("", cleaned)
    return cleaned.strip()


def has_prose_document(state: Mapping[str, Any]) -> bool:
    for row in iter_documents(state):
        if is_tabular_document(row):
            continue
        if (row.get("raw_text") or "").strip():
            return True
    combined = strip_tabular_stubs((state.get("document") or {}).get("raw_text") or "")
    return bool(combined)


def is_chitchat(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    compact = text.lower().replace("!", "").replace("~", "").strip()
    if len(compact) <= 12 and any(h in compact for h in CHITCHAT):
        return True
    return compact in {"안녕", "안녕하세요", "hello", "hi"}


def is_definition_question(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if any(h in text for h in DEFINITION_HINTS):
        return True
    if re.search(r"(이|가|은|는)\s*뭐야\s*\??$", text):
        if not any(k in text for k in ("파일", "데이터", "엑셀", "분석", "현황", "품목별", "공정", "지난주", "이번", "문제")):
            return True
    return False


def wants_quality_report(message: str, has_tabular: bool = False) -> bool:
    text = message or ""
    if is_chitchat(text) or is_definition_question(text):
        return False
    if any(k in text for k in ("초안", "드래프트")):
        return False
    lowered = text.lower()
    if not any(k in lowered for k in ("보고서", "리포트", "report")):
        return False
    return any(k in text for k in ("품질", "불량", "주간", "현황", "생산"))


def wants_data_analysis(message: str, has_tabular: bool) -> bool:
    text = message or ""
    lowered = text.lower()
    if is_chitchat(text) or is_definition_question(text):
        return False
    mentions_excel = any(k in lowered for k in ("엑셀", "excel", "xlsx", "xls", "csv"))
    analysis_verb = any(k in text for k in ANALYSIS_VERBS)
    quality = any(k in text for k in QUALITY_TERMS) or any(k in text for k in STRONG_ANALYSIS)
    if mentions_excel and analysis_verb:
        return True
    if quality and analysis_verb:
        return True
    if has_tabular and any(k in text for k in ("이 파일", "이 데이터", "이 엑셀")):
        return True
    if has_tabular and analysis_verb and any(k in text for k in ("파일", "데이터", "현황", "불량", "품질")):
        return True
    if has_tabular and any(k in text for k in ("호기", "상태", "어때", "원인", "왜")):
        return True
    return False


def is_casual_query(state: Mapping[str, Any]) -> bool:
    message = state.get("user_message") or ""
    return is_chitchat(message) or is_definition_question(message)


def should_execute_work(state: Mapping[str, Any]) -> bool:
    message = state.get("user_message") or ""
    if is_chitchat(message) or is_definition_question(message):
        return False
    if state.get("pending_clarification"):
        return True
    has_tabular = bool(tabular_documents(state))
    return wants_quality_report(message, has_tabular) or wants_data_analysis(message, has_tabular)


def classify_rule_intent(state: Mapping[str, Any]) -> dict[str, Any]:
    """명확한 경우만 확정한다. 애매하면 UNCERTAIN으로 Semantic Path에 넘긴다."""
    message = state.get("user_message") or ""
    has_tabular = bool(tabular_documents(state))
    has_prose = has_prose_document(state)
    has_actions = bool(state.get("actions"))

    if is_chitchat(message):
        return _rule("CONFIDENT_QUERY", "query", "일상 인사", "none", 0.99, "짧은 인사/잡담")
    if is_definition_question(message):
        return _rule("CONFIDENT_QUERY", "query", "용어 설명", "none", 0.95, "정의/의미 질문")
    if state.get("pending_clarification"):
        return _rule("CONFIDENT_EXECUTE", "execute", "이전 확인 사항 이어서 처리", "tabular_data_analysis", 0.97, "pending_clarification")
    if wants_quality_report(message, has_tabular):
        return _rule(
            "CONFIDENT_EXECUTE",
            "execute",
            "이번 주 품질 현황 보고서 작성",
            "tabular_data_analysis",
            0.94,
            "품질 보고서 작성 요청",
        )
    if wants_data_analysis(message, has_tabular):
        return _rule(
            "CONFIDENT_EXECUTE",
            "execute",
            "업로드된 데이터의 품질/현황 분석",
            "tabular_data_analysis",
            0.93,
            "명시적 데이터 분석 요청",
        )

    if any(h in message for h in QUERY_HINTS):
        return _rule("CONFIDENT_QUERY", "query", "남은 업무 확인", "none", 0.9, "진행 상황 질의")
    if has_actions and any(h in message for h in EXECUTE_HINTS):
        capability = "draft" if any(k in message for k in ("초안", "작성해", "드래프트")) else "none"
        return _rule("CONFIDENT_EXECUTE", "execute", "업무 실행", capability, 0.9, "초안/일정 실행 요청")
    if has_prose and any(h in message for h in REINGEST_HINTS):
        return _rule("CONFIDENT_INGEST", "ingest", "문서 재분석", "document_ingest", 0.92, "문서 재정리 요청")
    if has_prose and any(h in message for h in INGEST_HINTS):
        return _rule("CONFIDENT_INGEST", "ingest", "문서에서 할 일 추출", "document_ingest", 0.9, "명시적 문서 정리 요청")
    if has_actions and not has_tabular:
        return _rule("CONFIDENT_QUERY", "query", "등록된 업무 확인", "none", 0.8, "추출된 Action 기준 질의")
    return _rule("UNCERTAIN", None, "", "none", 0.0, "규칙만으로 확정할 수 없음")


def _rule(verdict: RuleVerdict, intent: str | None, goal: str, capability: str, confidence: float, reason: str) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "intent": intent,
        "goal": goal,
        "capability": capability,
        "confidence": confidence,
        "reason": reason,
    }


def match_clarification_choice(message: str, candidates: list[Any]) -> Any | None:
    picked = match_clarification_choice_rules(message, candidates)
    if picked is not None:
        return picked
    if _is_ambiguous_choice(message, candidates):
        return None
    return _semantic_clarification_choice(message, candidates)


def match_clarification_choice_rules(message: str, candidates: list[Any]) -> Any | None:
    """번호/서수/공백/조사 정규화로 후보 하나를 확정한다. 원본 후보를 반환한다."""
    text = (message or "").strip()
    if not text or not candidates:
        return None
    if _is_ambiguous_choice(text, candidates):
        return None

    labels = [_choice_label(item) for item in candidates]
    compact = _compact_choice_text(text)
    index = _index_from_number(compact)
    if index is None:
        index = _index_from_ordinal(compact)
    if index is not None and 0 <= index < len(candidates):
        return candidates[index]

    norm_message = normalize_choice_text(text)
    if not norm_message:
        return None

    exact = []
    contained = []
    for idx, item in enumerate(candidates):
        label = labels[idx]
        if not label:
            continue
        norm_label = normalize_choice_text(label)
        if not norm_label:
            continue
        if norm_label == norm_message:
            exact.append((idx, len(norm_label)))
        elif norm_label in norm_message:
            contained.append((idx, len(norm_label)))

    if len(exact) == 1:
        return candidates[exact[0][0]]
    if len(exact) > 1:
        return None
    if not contained:
        return None
    contained.sort(key=lambda row: -row[1])
    longest = contained[0][1]
    top = [idx for idx, length in contained if length == longest]
    if len(top) != 1:
        return None
    return candidates[top[0]]


def normalize_choice_text(text: str) -> str:
    compact = _compact_choice_text(text)
    changed = True
    while changed and compact:
        changed = False
        for tail in _CHOICE_TAILS:
            if compact.endswith(tail) and len(compact) > len(tail):
                compact = compact[: -len(tail)]
                changed = True
                break
        if changed:
            continue
        stripped = _CHOICE_PARTICLE.sub("", compact)
        if stripped and stripped != compact:
            compact = stripped
            changed = True
    return compact


def _choice_label(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("filename") or item.get("id") or "")
    return str(item or "")


def _compact_choice_text(text: str) -> str:
    return re.sub(r"[\s\-_/·.,]+", "", (text or "").strip().lower())


def _index_from_number(compact: str) -> int | None:
    match = _CHOICE_NUMBER.fullmatch(compact)
    if not match:
        return None
    return int(match.group(1)) - 1


def _index_from_ordinal(compact: str) -> int | None:
    for word, number in _CHOICE_ORDINALS:
        if compact == word or compact.startswith(word):
            rest = compact[len(word) :]
            if rest in {"", "거", "걸로", "요", "으로", "로", "해줘", "으로해줘"}:
                return number - 1
    return None


def _is_ambiguous_choice(message: str, candidates: list[Any] | None = None) -> bool:
    compact = _compact_choice_text(message)
    if not compact:
        return False
    if any(token in compact for token in _AMBIGUOUS_CHOICE):
        return True
    if candidates:
        hits = [
            idx
            for idx, item in enumerate(candidates)
            if (label := normalize_choice_text(_choice_label(item))) and label in normalize_choice_text(message)
        ]
        unique = {normalize_choice_text(_choice_label(candidates[i])) for i in hits}
        if len(unique) >= 2:
            longest = max(unique, key=len)
            others = [item for item in unique if item != longest and item not in longest]
            if others:
                return True
    return False


def _semantic_clarification_choice(message: str, candidates: list[Any]) -> Any | None:
    try:
        from app.agent.semantic_intent import resolve_semantic_clarification

        return resolve_semantic_clarification(message, candidates)
    except Exception:
        return None
