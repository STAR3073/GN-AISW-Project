"""제조 Excel/CSV 품질 데이터 분석 Tool.

업로드된 파일만 읽고, 수식/매크로/임의 경로/코드 실행은 하지 않는다.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping

from app.config import get_settings

SUPPORTED_SUFFIXES = {".xlsx", ".xls", ".csv"}
MAX_ROWS = 100_000
TOP_N = 5
AUTO_MAP_MIN = 0.8
STRONG_MAP_MIN = 0.75

ROLES = (
    "item",
    "lot",
    "defect_qty",
    "production_qty",
    "inspection_qty",
    "input_qty",
    "good_qty",
    "defect_rate",
    "result",
    "defect_type",
    "process",
    "equipment",
    "date",
)

QTY_ROLES = ("defect_qty", "production_qty", "inspection_qty", "input_qty", "good_qty")
DIMENSION_ROLES = ("item", "process", "equipment")
OPTIONAL_CLARIFY_ROLES = {"defect_rate"}
RATE_EXCLUSION_TOKENS = ("비율", "percentage", "percent", "ratio", "rate", "pct", "률", "율", "%")
METRIC_EXCLUSION_TOKENS = (
    "횟수",
    "수량",
    "건수",
    "개수",
    "count",
    "qty",
    "quantity",
    "rate",
    "ratio",
    "pct",
    "percentage",
    "률",
    "율",
    "비율",
    "%",
)

ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "item": ("품목", "품목명", "item", "item_name", "itemname", "제품명", "제품", "자재명", "자재", "part_no", "partno"),
    "lot": ("lot_no", "lotno", "생산lot", "lot"),
    "defect_qty": (
        "불량수량",
        "불량건수",
        "불량수",
        "ng_qty",
        "ngqty",
        "defect_qty",
        "불량량",
        "검사불량",
        "최종ng",
        "ng수량",
        "ng수",
    ),
    "production_qty": (
        "생산수량",
        "생산량",
        "생산실적",
        "prod_qty",
        "prodqty",
        "생산수",
    ),
    "inspection_qty": ("검사수량", "검사량", "inspection_qty", "inspected_qty", "inspqty"),
    "input_qty": ("투입수량", "투입량", "input_qty", "투입수"),
    "good_qty": ("양품수량", "양품수", "ok수량", "good_qty", "ok_qty", "goodqty", "okqty"),
    "defect_rate": (
        "불량률",
        "불량율",
        "불량비율",
        "ng율",
        "ng률",
        "ng비율",
        "defect_rate",
        "defect_ratio",
        "ng_rate",
        "ng_ratio",
        "defectrate",
        "ngrate",
    ),
    "result": ("판정결과", "ok_ng", "okng", "합격여부", "합불", "판정", "result"),
    "defect_type": ("불량유형", "불량명", "불량내용", "불량코드", "defect_type", "ng_type", "defect_code"),
    "process": ("공정명", "process_name", "공정코드", "공정", "process"),
    "equipment": ("설비명", "equipment", "machine", "설비번호", "설비", "호기"),
    "date": ("생산일자", "검사일자", "work_date", "datetime", "생산일", "검사일", "일자", "날짜", "date"),
}

ROLE_LABELS = {
    "item": "품목",
    "lot": "LOT",
    "defect_qty": "불량수량",
    "production_qty": "생산수량",
    "inspection_qty": "검사수량",
    "input_qty": "투입수량",
    "good_qty": "양품수량",
    "defect_rate": "불량률",
    "result": "판정",
    "defect_type": "불량유형",
    "process": "공정",
    "equipment": "설비",
    "date": "날짜",
}

_OK_VALUES = {"ok", "o", "y", "yes", "pass", "true", "1", "합격", "양품", "정상", "good"}
_NG_VALUES = {"ng", "n", "no", "fail", "false", "0", "불합격", "불량", "이상", "bad", "reject"}


def analyze_excel(args: dict[str, Any] | None, state: Mapping[str, Any] | None) -> dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    state = state or {}
    resolved, error = resolve_excel_source(args, state)
    if error:
        return error
    return inspect_and_analyze(
        path=resolved["path"],
        filename=resolved["filename"],
        document_id=resolved.get("document_id") or "",
        sheet=args.get("sheet") or args.get("sheet_name"),
        column_overrides=args.get("column_mapping") if isinstance(args.get("column_mapping"), dict) else {},
        top_n=int(args.get("top_n") or TOP_N),
        user_message=str(state.get("user_message") or ""),
        row_filter=args.get("filter") if isinstance(args.get("filter"), dict) else None,
    )


def resolve_excel_source(args: dict[str, Any], state: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """document/attachment id 또는 세션 문서만 허용. args.path는 무시한다."""
    wanted_id = str(args.get("document_id") or "").strip()
    wanted_att = str(args.get("attachment_id") or "").strip()
    candidates = _collect_candidates(state, wanted_id, wanted_att)

    tabular = []
    skipped_unsupported = []
    for item in candidates:
        path = _safe_stored_path(item.get("stored_path") or "")
        filename = item.get("filename") or (path.name if path else "")
        suffix = Path(filename or (path or Path(""))).suffix.lower()
        record = {
            "id": item.get("id") or "",
            "filename": filename,
            "stored_path": str(path) if path else item.get("stored_path") or "",
            "path": path,
            "suffix": suffix,
        }
        if suffix and suffix not in SUPPORTED_SUFFIXES:
            skipped_unsupported.append(record)
            continue
        if path is None:
            if item.get("stored_path"):
                return None, _fail(
                    "파일을 찾을 수 없습니다.",
                    error="file_not_found",
                    file={"id": record["id"], "filename": filename, "path_allowed": False},
                )
            continue
        if suffix not in SUPPORTED_SUFFIXES:
            skipped_unsupported.append(record)
            continue
        tabular.append({**record, "document_id": record["id"]})

    if wanted_id or wanted_att:
        if not tabular and skipped_unsupported:
            bad = skipped_unsupported[0]
            return None, _fail(
                f"지원하지 않는 파일 형식입니다: {bad['suffix'] or Path(bad['filename']).suffix or 'unknown'}",
                error="unsupported_type",
                file={"id": bad["id"], "filename": bad["filename"]},
            )
        if not tabular:
            return None, _fail("파일을 찾을 수 없습니다.", error="file_not_found")

    if not tabular:
        if skipped_unsupported and not wanted_id:
            bad = skipped_unsupported[0]
            return None, _fail(
                f"지원하지 않는 파일 형식입니다: {Path(bad['filename']).suffix.lower() or 'unknown'}",
                error="unsupported_type",
                file={"id": bad["id"], "filename": bad["filename"]},
            )
        return None, _fail(
            "분석할 Excel/CSV 파일을 찾지 못했습니다. 프로젝트 또는 현재 대화에 xlsx/xls/csv를 첨부하세요.",
            error="file_not_found",
        )

    if len(tabular) > 1 and not wanted_id and not wanted_att:
        return None, _fail(
            "분석할 Excel/CSV 파일이 여러 개입니다. document_id로 파일을 지정하세요.",
            error="multiple_files",
            needs_clarification=True,
            clarification={
                "field": "document_id",
                "message": "분석할 파일을 선택하세요.",
                "candidates": [
                    {"id": t["id"], "filename": t["filename"]} for t in tabular
                ],
            },
        )

    chosen = tabular[0]
    return chosen, None


def inspect_and_analyze(
    path: Path,
    filename: str = "",
    document_id: str = "",
    sheet: str | None = None,
    column_overrides: dict[str, Any] | None = None,
    top_n: int = TOP_N,
    user_message: str = "",
    row_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filename = filename or path.name
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return _fail(
            f"지원하지 않는 파일 형식입니다: {suffix or 'unknown'}",
            error="unsupported_type",
            file={"id": document_id, "filename": filename},
        )
    if not path.exists() or not path.is_file():
        return _fail("파일을 찾을 수 없습니다.", error="file_not_found", file={"id": document_id, "filename": filename})

    settings = get_settings()
    try:
        size = path.stat().st_size
    except OSError:
        return _fail("파일을 찾을 수 없습니다.", error="file_not_found", file={"id": document_id, "filename": filename})
    if size > int(settings.max_upload_bytes):
        return _fail("파일이 너무 커서 분석할 수 없습니다.", error="file_too_large", file={"id": document_id, "filename": filename})
    if size == 0:
        return _fail("빈 파일입니다.", error="empty_file", file={"id": document_id, "filename": filename})

    try:
        sheets, selected, frame, truncated = _load_frame(path, sheet)
    except ValueError as exc:
        code = str(exc)
        if code == "empty_sheet":
            return _fail(
                "선택한 Sheet에 데이터가 없습니다.",
                error="empty_sheet",
                file={"id": document_id, "filename": filename, "sheets": _sheet_names_only(path)},
            )
        if code == "unsupported_type":
            return _fail(f"지원하지 않는 파일 형식입니다: {suffix}", error="unsupported_type")
        if code == "corrupt":
            return _fail(
                "Excel/CSV 파일을 읽지 못했습니다. 파일이 손상되었거나 암호로 보호되어 있을 수 있습니다.",
                error="corrupt_file",
                file={"id": document_id, "filename": filename},
            )
        return _fail(str(exc), error="read_error", file={"id": document_id, "filename": filename})
    except Exception:
        return _fail(
            "Excel/CSV 파일을 읽지 못했습니다. 파일이 손상되었거나 암호로 보호되어 있을 수 있습니다.",
            error="corrupt_file",
            file={"id": document_id, "filename": filename},
        )

    warnings: list[str] = []
    if truncated:
        warnings.append(f"행이 {MAX_ROWS}건을 넘어 앞부분만 분석했습니다.")

    schema = _inspect_schema(frame, sheets, selected)
    if schema["row_count"] == 0:
        return _fail(
            "선택한 Sheet에 데이터가 없습니다.",
            error="empty_sheet",
            file={"id": document_id, "filename": filename, "sheet": selected},
            schema=schema,
        )

    missing_header = _looks_like_missing_header(schema["columns"])
    if missing_header:
        warnings.append("헤더가 없거나 컬럼명을 신뢰할 수 없습니다.")

    mapping, clarify_roles = _map_columns(schema["columns"], column_overrides or {})
    mapping, clarify_roles, map_warnings = _sanitize_qty_rate_mapping(
        mapping, schema["columns"], column_overrides or {}, clarify_roles
    )
    warnings.extend(map_warnings)
    stale_overrides = _stale_override_roles(schema["columns"], column_overrides or {})
    if stale_overrides:
        warnings.append("저장해 둔 일부 컬럼명이 현재 파일에 없어 해당 항목만 다시 확인합니다.")
    needs_clarification = bool(clarify_roles) or missing_header
    clarification = None
    if clarify_roles:
        first = clarify_roles[0]
        clarification = {
            "field": first,
            "message": _clarification_message(first, mapping.get(first) or {}),
            "candidates": list((mapping.get(first) or {}).get("candidates") or []),
        }
        extra = " 후보: " + ", ".join(clarification["candidates"]) if clarification["candidates"] else ""
        warnings.append(clarification["message"] + extra)
    elif missing_header:
        clarification = {
            "field": "header",
            "message": "헤더 행을 확정할 수 없습니다.",
            "candidates": schema["columns"],
        }
        needs_clarification = True

    summary = _basic_summary(frame, schema)
    quality = None
    top_defects: list[dict[str, Any]] = []
    group_analysis: dict[str, Any] = {}
    period_analysis = None
    change_detection = None
    filter_context = None
    within_filter = None
    candidate_factors: list[dict[str, Any]] = []
    additional_data_suggestions: list[str] = []

    can_analyze = not missing_header
    if can_analyze:
        metrics, m_warnings = _run_metrics(frame, mapping, max(1, top_n))
        warnings.extend(m_warnings)
        quality = metrics["quality_metrics"]
        top_defects = metrics["top_defects"]
        group_analysis = metrics["group_analysis"]
        period_analysis = metrics["period_analysis"]
        change_detection = metrics["change_detection"]
        filter_context, within_filter, f_warnings = _apply_row_filter(frame, mapping, row_filter, max(1, top_n))
        warnings.extend(f_warnings)
        scoped = within_filter if filter_context and filter_context.get("matched") and within_filter else metrics
        candidate_factors = _candidate_factors(scoped, filter_context=filter_context)
        if within_filter is not None:
            within_filter = {**within_filter, "candidate_factors": candidate_factors}
        additional_data_suggestions = _additional_data_suggestions(
            schema.get("columns") or [],
            (filter_context or {}).get("role"),
        )

    if quality is None and can_analyze:
        if not mapping["defect_qty"]["column"] and not mapping["result"]["column"]:
            warnings.append("불량 관련 컬럼이 없어 품질 지표는 계산하지 않았습니다.")
        if not mapping["production_qty"]["column"]:
            warnings.append("생산수량 컬럼이 없어 불량률(불량수량/생산수량)은 계산하지 않았습니다.")
            if mapping["inspection_qty"]["column"]:
                warnings.append("검사수량 컬럼은 확인했지만 생산수량과 같은 의미로 가정하지 않았습니다.")

    detail = _detail_message(
        filename=filename,
        schema=schema,
        quality=quality,
        needs_clarification=needs_clarification,
        warnings=warnings,
    )
    return {
        "ok": True,
        "file": {
            "id": document_id,
            "filename": filename,
            "suffix": suffix,
            "sheet": selected,
        },
        "schema": schema,
        "column_mapping": mapping,
        "summary": summary,
        "quality_metrics": quality,
        "top_defects": top_defects,
        "group_analysis": group_analysis,
        "period_analysis": period_analysis,
        "change_detection": change_detection,
        "filter_context": filter_context,
        "within_filter": within_filter,
        "candidate_factors": candidate_factors,
        "additional_data_suggestions": additional_data_suggestions,
        "warnings": warnings,
        "needs_clarification": needs_clarification,
        "clarification": clarification,
        "stale_overrides": stale_overrides,
        "detail": detail,
        "error": None,
        "user_message": user_message[:200],
    }


def _collect_candidates(state: Mapping[str, Any], document_id: str, attachment_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: dict[str, Any] | None) -> None:
        if not item:
            return
        key = str(item.get("id") or item.get("stored_path") or "")
        if key and key in seen:
            return
        if key:
            seen.add(key)
        found.append(item)

    if document_id:
        for row in _iter_state_docs(state):
            if str(row.get("id") or "") == document_id:
                add(row)
        add(_lookup_document(document_id, str(state.get("session_id") or "") or None))
        return found

    if attachment_id:
        add(_lookup_attachment(attachment_id))
        return found

    for row in _iter_state_docs(state):
        add(row)

    session_id = str(state.get("session_id") or "")
    if session_id:
        for row in _lookup_session_tabular(session_id):
            add(row)
    return found


def _iter_state_docs(state: Mapping[str, Any]):
    for row in state.get("documents") or []:
        if isinstance(row, dict):
            yield row
    doc = state.get("document")
    if isinstance(doc, dict) and (doc.get("id") or doc.get("stored_path") or doc.get("filename")):
        yield doc


def _lookup_document(document_id: str, session_id: str | None) -> dict[str, Any] | None:
    from app.db.database import SessionLocal
    from app.models import AgentSession, ChatDocument, Document

    db = SessionLocal()
    try:
        row = db.get(Document, document_id)
        if not row:
            return None
        if session_id:
            session = db.get(AgentSession, session_id)
            if not session:
                return None
            linked = (
                db.query(ChatDocument)
                .filter(ChatDocument.session_id == session_id, ChatDocument.document_id == document_id)
                .first()
            )
            same_project = bool(session.project_id and row.project_id and session.project_id == row.project_id)
            primary = session.document_id == document_id
            if not (linked or same_project or primary):
                return None
        return {
            "id": row.id,
            "filename": row.filename,
            "stored_path": row.stored_path,
            "mime": row.mime,
        }
    finally:
        db.close()


def _lookup_attachment(attachment_id: str) -> dict[str, Any] | None:
    from app.db.database import SessionLocal
    from app.models import Attachment

    db = SessionLocal()
    try:
        row = db.get(Attachment, attachment_id)
        if not row:
            return None
        return {
            "id": row.id,
            "filename": row.filename,
            "stored_path": row.stored_path,
            "mime": row.mime,
        }
    finally:
        db.close()


def _lookup_session_tabular(session_id: str) -> list[dict[str, Any]]:
    from app.db.database import SessionLocal
    from app.models import AgentSession, ChatDocument, Document

    db = SessionLocal()
    try:
        session = db.get(AgentSession, session_id)
        if not session:
            return []
        rows: list[Document] = []
        links = db.query(ChatDocument).filter(ChatDocument.session_id == session_id).all()
        for link in links:
            doc = db.get(Document, link.document_id)
            if doc:
                rows.append(doc)
        if session.document_id:
            primary = db.get(Document, session.document_id)
            if primary and all(r.id != primary.id for r in rows):
                rows.append(primary)
        out = []
        for row in rows:
            suffix = Path(row.filename or "").suffix.lower()
            if suffix in SUPPORTED_SUFFIXES:
                out.append(
                    {
                        "id": row.id,
                        "filename": row.filename,
                        "stored_path": row.stored_path,
                        "mime": row.mime,
                    }
                )
        return out
    finally:
        db.close()


def _safe_stored_path(stored: str) -> Path | None:
    if not stored or not isinstance(stored, str):
        return None
    try:
        path = Path(stored).resolve()
    except OSError:
        return None
    if not path.exists() or not path.is_file():
        return None
    settings = get_settings()
    roots = [Path(settings.upload_dir).resolve(), Path(settings.convert_dir).resolve()]
    for root in roots:
        try:
            path.relative_to(root)
            return path
        except ValueError:
            continue
    return None


def _load_frame(path: Path, sheet: str | None):
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = _read_csv(path)
        sheets = [{"name": path.stem, "row_count": int(len(frame)), "column_count": int(frame.shape[1])}]
        selected = path.stem
        truncated = len(frame) > MAX_ROWS
        if truncated:
            frame = frame.iloc[:MAX_ROWS].copy()
        return sheets, selected, frame, truncated

    engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
    try:
        book = pd.ExcelFile(path, engine=engine)
    except ImportError as exc:
        raise ValueError("이 환경에서 .xls를 읽으려면 xlrd가 필요합니다.") from exc
    except Exception as exc:
        raise ValueError("corrupt") from exc

    try:
        sheets = []
        nonempty: list[str] = []
        for name in book.sheet_names:
            preview = book.parse(name, nrows=MAX_ROWS + 1)
            row_count = int(len(preview))
            sheets.append({"name": str(name), "row_count": row_count, "column_count": int(preview.shape[1])})
            if row_count > 0 and preview.shape[1] > 0:
                nonempty.append(str(name))

        if sheet:
            selected = str(sheet)
            if selected not in book.sheet_names:
                raise ValueError(f"Sheet '{selected}'를 찾을 수 없습니다.")
        elif nonempty:
            selected = nonempty[0]
        elif book.sheet_names:
            selected = str(book.sheet_names[0])
        else:
            raise ValueError("empty_sheet")

        frame = book.parse(selected)
        truncated = len(frame) > MAX_ROWS
        if truncated:
            frame = frame.iloc[:MAX_ROWS].copy()
        if frame.empty or frame.shape[1] == 0:
            raise ValueError("empty_sheet")
        return sheets, selected, frame, truncated
    finally:
        book.close()


def _sheet_names_only(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return [path.stem]
    try:
        import pandas as pd

        engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
        book = pd.ExcelFile(path, engine=engine)
        try:
            return [str(n) for n in book.sheet_names]
        finally:
            book.close()
    except Exception:
        return []


def _read_csv(path: Path):
    import pandas as pd

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=encoding, nrows=MAX_ROWS + 1)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            continue
    raise last_error or ValueError("csv_read_error")


def _inspect_schema(frame, sheets: list[dict[str, Any]], selected: str) -> dict[str, Any]:
    columns = [str(c) for c in frame.columns]
    dtypes = {}
    missing = {}
    numeric_cols = []
    string_cols = []
    date_cols = []
    for col in columns:
        series = frame[col]
        kind = _column_kind(series)
        dtypes[col] = kind
        missing[col] = int(series.isna().sum())
        if kind == "number":
            numeric_cols.append(col)
        elif kind == "date":
            date_cols.append(col)
        else:
            string_cols.append(col)
            if _date_parse_ratio(series) >= 0.6:
                date_cols.append(col)
    return {
        "sheets": sheets,
        "selected_sheet": selected,
        "row_count": int(len(frame)),
        "column_count": int(len(columns)),
        "columns": columns,
        "dtypes": dtypes,
        "missing_counts": missing,
        "numeric_columns": numeric_cols,
        "string_columns": string_cols,
        "date_columns": list(dict.fromkeys(date_cols)),
    }


def _column_kind(series) -> str:
    import pandas as pd

    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    return "string"


def _date_parse_ratio(series) -> float:
    import pandas as pd

    sample = series.dropna()
    if sample.empty:
        return 0.0
    if len(sample) > 200:
        sample = sample.iloc[:200]
    parsed = pd.to_datetime(sample.astype(str).str.strip(), errors="coerce", format="mixed")
    return float(parsed.notna().mean())


def _looks_like_missing_header(columns: list[str]) -> bool:
    if not columns:
        return True
    weak = 0
    for col in columns:
        text = str(col).strip()
        if not text or text.lower().startswith("unnamed"):
            weak += 1
            continue
        try:
            float(text.replace(",", ""))
            weak += 1
        except ValueError:
            pass
    return weak >= max(1, int(math.ceil(0.8 * len(columns))))


def _norm(name: str) -> str:
    text = str(name).strip().lower()
    text = text.replace(" ", "").replace("_", "").replace("-", "")
    return text


def _looks_like_rate_column(name: str) -> bool:
    raw = str(name or "")
    if "%" in raw or "％" in raw:
        return True
    col = _norm(raw)
    if not col:
        return False
    return any(_norm(token) and _norm(token) in col for token in RATE_EXCLUSION_TOKENS)


def _looks_like_metric_column(name: str) -> bool:
    raw = str(name or "")
    if "%" in raw or "％" in raw:
        return True
    col = _norm(raw)
    if not col:
        return False
    return any(_norm(token) and _norm(token) in col for token in METRIC_EXCLUSION_TOKENS)


def _stale_override_roles(columns: list[str], overrides: Mapping[str, Any]) -> list[str]:
    known = {str(col) for col in columns}
    stale: list[str] = []
    for role, raw in dict(overrides or {}).items():
        col = str(raw or "").strip()
        if str(role or "").strip() and col and col not in known:
            stale.append(str(role))
    return stale


def _alias_score(column: str, alias: str) -> float:
    col = _norm(column)
    ali = _norm(alias)
    if not col or not ali:
        return 0.0
    if col == ali:
        return 1.0
    if ali.isascii() and len(ali) <= 3:
        return 0.0
    if ali in col:
        return 0.8
    if col in ali and len(col) >= 4:
        return 0.65
    return 0.0


def _column_role_score(column: str, role: str) -> float:
    if role in QTY_ROLES and _looks_like_rate_column(column):
        return 0.0
    if role in DIMENSION_ROLES and _looks_like_metric_column(column):
        return 0.0
    aliases = ROLE_ALIASES.get(role) or ()
    if not aliases:
        return 0.0
    return max(_alias_score(column, alias) for alias in aliases)


def _strong_candidates_for_role(role: str, columns: list[str], used_cols: set[str]) -> list[str]:
    ranked: list[tuple[str, float]] = []
    for col in columns:
        if col in used_cols:
            continue
        score = _column_role_score(col, role)
        if score >= STRONG_MAP_MIN:
            ranked.append((col, score))
    ranked.sort(key=lambda item: (-item[1], -len(_norm(item[0]))))
    return [col for col, _score in ranked[:8]]


def _empty_role() -> dict[str, Any]:
    return {"column": None, "confidence": 0.0, "status": "missing", "candidates": []}


def _clarification_message(role: str, info: Mapping[str, Any]) -> str:
    label = ROLE_LABELS.get(role, role)
    rejected = info.get("rejected_column")
    if info.get("rejected_reason") == "rate_as_qty" and rejected:
        return f"'{rejected}'는 비율 컬럼이라 {label}으로 사용할 수 없습니다. 수량 컬럼을 지정해 주세요."
    return f"{label}으로 사용할 컬럼을 확정할 수 없습니다."


def _map_columns(columns: list[str], overrides: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    mapping: dict[str, Any] = {}
    used_cols: set[str] = set()
    for role in ROLES:
        mapping[role] = _empty_role()

    for role, raw in overrides.items():
        if role not in mapping:
            continue
        col = str(raw).strip()
        if col in columns:
            mapping[role] = {"column": col, "confidence": 1.0, "status": "mapped", "candidates": [col]}
            used_cols.add(col)

    scores: dict[str, dict[str, float]] = {role: {} for role in ROLES}
    for col in columns:
        if col in used_cols:
            continue
        for role in ROLES:
            if mapping[role]["status"] == "mapped":
                continue
            best = _column_role_score(col, role)
            if best > 0:
                scores[role][col] = best

    owned: dict[str, list[tuple[str, float]]] = {role: [] for role in ROLES}
    for col in columns:
        if col in used_cols:
            continue
        ranked = sorted(
            ((scores[role].get(col, 0.0), role) for role in ROLES if mapping[role]["status"] != "mapped"),
            reverse=True,
        )
        ranked = [(sc, role) for sc, role in ranked if sc > 0]
        if not ranked:
            continue
        top_score, top_role = ranked[0]
        if len(ranked) > 1 and ranked[1][0] == top_score:
            continue
        owned[top_role].append((col, top_score))

    clarify: list[str] = []
    for role in ROLES:
        if mapping[role]["status"] == "mapped":
            continue
        ranked = sorted(owned[role], key=lambda item: (-item[1], -len(_norm(item[0]))))
        strong = [(col, sc) for col, sc in ranked if sc >= STRONG_MAP_MIN]
        mapping[role]["candidates"] = [col for col, _sc in strong[:8]]
        if len(strong) >= 2:
            mapping[role]["status"] = "ambiguous"
            mapping[role]["confidence"] = _json_num(strong[0][1])
            clarify.append(role)
            continue
        if len(strong) == 1 and strong[0][1] >= AUTO_MAP_MIN:
            col, sc = strong[0]
            mapping[role] = {
                "column": col,
                "confidence": _json_num(sc),
                "status": "mapped",
                "candidates": [col],
            }
            used_cols.add(col)
    clarify = [role for role in clarify if role not in OPTIONAL_CLARIFY_ROLES]
    return mapping, clarify


def _mapped_columns(mapping: Mapping[str, Any]) -> set[str]:
    used: set[str] = set()
    for info in mapping.values():
        if isinstance(info, dict) and info.get("status") == "mapped" and info.get("column"):
            used.add(str(info["column"]))
    return used


def _try_map_role(mapping: dict[str, Any], role: str, columns: list[str], used_cols: set[str]) -> None:
    if mapping.get(role, {}).get("status") == "mapped":
        return
    candidates = _strong_candidates_for_role(role, columns, used_cols)
    mapping[role]["candidates"] = candidates
    if len(candidates) == 1:
        mapping[role] = {
            "column": candidates[0],
            "confidence": 1.0,
            "status": "mapped",
            "candidates": candidates,
        }
        used_cols.add(candidates[0])


def _sanitize_qty_rate_mapping(
    mapping: dict[str, Any],
    columns: list[str],
    overrides: Mapping[str, Any],
    clarify_roles: list[str],
) -> tuple[dict[str, Any], list[str], list[str]]:
    warnings: list[str] = []
    extra_clarify: list[str] = []
    used_cols = _mapped_columns(mapping)
    for role in QTY_ROLES:
        info = mapping.get(role) or {}
        col = info.get("column")
        if not col or not _looks_like_rate_column(str(col)):
            continue
        rejected = str(col)
        overridden = str(overrides.get(role) or "").strip() == rejected
        used_cols.discard(rejected)
        candidates = _strong_candidates_for_role(role, columns, used_cols)
        mapping[role] = {
            "column": None,
            "confidence": 0.0,
            "status": "missing",
            "candidates": candidates,
            "rejected_column": rejected,
            "rejected_reason": "rate_as_qty",
        }
        label = ROLE_LABELS.get(role, role)
        if overridden:
            mapping[role]["status"] = "ambiguous"
            extra_clarify.append(role)
            warnings.append(f"'{rejected}'는 비율 컬럼이라 {label}으로 합산하지 않았습니다.")
        else:
            warnings.append(f"'{rejected}'는 비율 컬럼이라 {label} 자동 매핑에서 제외했습니다.")
            if len(candidates) >= 2:
                mapping[role]["status"] = "ambiguous"
                extra_clarify.append(role)
            elif len(candidates) == 1 and _column_role_score(candidates[0], role) >= AUTO_MAP_MIN:
                mapping[role] = {
                    "column": candidates[0],
                    "confidence": 1.0,
                    "status": "mapped",
                    "candidates": candidates[:1],
                }
                used_cols.add(candidates[0])
    _try_map_role(mapping, "defect_rate", columns, used_cols)
    ordered: list[str] = []
    for role in extra_clarify + list(clarify_roles):
        if role in OPTIONAL_CLARIFY_ROLES or role in ordered:
            continue
        if mapping.get(role, {}).get("status") == "ambiguous":
            ordered.append(role)
    return mapping, ordered, warnings


def _to_numeric(series):
    import pandas as pd

    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = series.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip()
    cleaned = cleaned.replace({"": None, "nan": None, "None": None, "-": None})
    return pd.to_numeric(cleaned, errors="coerce")


def _result_mask(series):
    import pandas as pd

    text = series.astype(str).str.strip().str.lower()
    ok = text.isin(_OK_VALUES)
    ng = text.isin(_NG_VALUES)
    return ok, ng, ~(ok | ng) & series.notna()


def _basic_summary(frame, schema: dict[str, Any]) -> dict[str, Any]:
    numeric_stats = {}
    coerced_warnings = []
    for col in schema["numeric_columns"]:
        nums = _to_numeric(frame[col]).dropna()
        if nums.empty:
            continue
        numeric_stats[col] = {
            "count": int(nums.count()),
            "mean": _json_num(nums.mean()),
            "min": _json_num(nums.min()),
            "max": _json_num(nums.max()),
            "std": _json_num(nums.std()) if len(nums) > 1 else 0.0,
        }
    for col in schema["columns"]:
        if col in schema["numeric_columns"]:
            continue
        raw_non_null = int(frame[col].notna().sum())
        nums = _to_numeric(frame[col])
        numeric_ok = int(nums.notna().sum())
        if raw_non_null and numeric_ok >= 3 and numeric_ok < raw_non_null and numeric_ok / raw_non_null >= 0.5:
            if _date_parse_ratio(frame[col]) < 0.5:
                coerced_warnings.append(
                    f"컬럼 '{col}'에 숫자로 변환하지 못한 값이 {raw_non_null - numeric_ok}건 있습니다."
                )
    dup = int(frame.duplicated().sum())
    return {
        "row_count": schema["row_count"],
        "duplicate_rows": dup,
        "missing_counts": schema["missing_counts"],
        "numeric_stats": numeric_stats,
        "coercion_notes": coerced_warnings,
    }


def _qty_mapped_column(mapping: Mapping[str, Any], role: str) -> str | None:
    col = (mapping.get(role) or {}).get("column")
    if not col or (role in QTY_ROLES and _looks_like_rate_column(str(col))):
        return None
    return str(col)


def _quality_metrics(frame, mapping: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    prod_col = _qty_mapped_column(mapping, "production_qty")
    defect_col = _qty_mapped_column(mapping, "defect_qty")
    result_col = mapping["result"]["column"]
    inspect_col = mapping["inspection_qty"]["column"]
    input_col = mapping["input_qty"]["column"]
    good_col = mapping["good_qty"]["column"]
    rate_col = mapping["defect_rate"]["column"]
    if not prod_col and not defect_col and not result_col:
        extra = []
        if inspect_col:
            extra.append("검사수량 컬럼은 확인했지만 생산수량과 같은 의미로 가정하지 않았습니다.")
        if rate_col:
            extra.append(f"불량률 컬럼 '{rate_col}'은 참고용으로만 인식했으며 합산하지 않았습니다.")
        return None, extra

    payload: dict[str, Any] = {
        "inspected_rows": int(len(frame)),
        "production_qty_total": None,
        "inspection_qty_total": None,
        "input_qty_total": None,
        "good_qty_total": None,
        "defect_qty_total": None,
        "defect_rate_pct": None,
        "formula": None,
        "ok_count": None,
        "ng_count": None,
        "inspection_fail_rate_pct": None,
        "source_defect_rate": None,
    }

    def _sum_qty(column: str, label: str) -> float | int | None:
        nums = _to_numeric(frame[column])
        invalid = int(frame[column].notna().sum() - nums.notna().sum())
        if invalid:
            warnings.append(f"{label} 컬럼 '{column}'에서 숫자로 바꾸지 못한 값이 {invalid}건 있습니다.")
        return _json_num(nums.fillna(0).sum())

    if prod_col:
        payload["production_qty_total"] = _sum_qty(prod_col, "생산수량")
    if inspect_col:
        payload["inspection_qty_total"] = _sum_qty(inspect_col, "검사수량")
    if input_col:
        payload["input_qty_total"] = _sum_qty(input_col, "투입수량")
    if good_col:
        payload["good_qty_total"] = _sum_qty(good_col, "양품수량")
    if defect_col:
        payload["defect_qty_total"] = _sum_qty(defect_col, "불량수량")

    if (
        payload["production_qty_total"] is not None
        and payload["defect_qty_total"] is not None
        and payload["production_qty_total"] > 0
    ):
        payload["defect_rate_pct"] = _json_num(
            payload["defect_qty_total"] / payload["production_qty_total"] * 100
        )
        payload["formula"] = "불량수량 / 생산수량 × 100"
    elif payload["production_qty_total"] == 0:
        warnings.append("생산수량 합이 0이라 불량률은 계산하지 않았습니다.")
    elif defect_col and not prod_col:
        warnings.append("생산수량 컬럼이 없어 불량률(불량수량/생산수량)은 계산하지 않았습니다.")

    if inspect_col and not prod_col:
        warnings.append("검사수량 컬럼은 확인했지만 생산수량과 같은 의미로 가정하지 않았습니다.")

    if rate_col:
        payload["source_defect_rate"] = {
            "column": rate_col,
            "aggregated": False,
            "used_for_primary_metric": False,
        }
        if payload["defect_rate_pct"] is not None:
            payload["defect_rate_comparison"] = {
                "primary": payload["defect_rate_pct"],
                "primary_formula": payload["formula"],
                "source_column": rate_col,
                "source_aggregated": False,
                "note": "원본 불량률 컬럼은 참고용이며 합산하지 않았습니다.",
            }
        else:
            warnings.append(f"불량률 컬럼 '{rate_col}'은 확인했지만 불량수량/생산수량 기준으로 재계산하지 못했습니다.")

    if result_col:
        ok_mask, ng_mask, unknown = _result_mask(frame[result_col])
        payload["ok_count"] = int(ok_mask.sum())
        payload["ng_count"] = int(ng_mask.sum())
        known = payload["ok_count"] + payload["ng_count"]
        if known:
            payload["inspection_fail_rate_pct"] = _json_num(payload["ng_count"] / known * 100)
        unknown_n = int(unknown.sum())
        if unknown_n:
            warnings.append(f"판정 컬럼 '{result_col}'에서 OK/NG로 읽지 못한 값이 {unknown_n}건 있습니다.")
        if payload["defect_rate_pct"] is None:
            warnings.append("OK/NG 판정은 검사 불합격률로만 집계했습니다. 생산수량 기반 불량률은 아닙니다.")

    if all(payload[k] is None for k in ("production_qty_total", "defect_qty_total", "ng_count")):
        return None, warnings
    return payload, warnings


def _run_metrics(frame, mapping: dict[str, Any], top_n: int) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    quality, q_warnings = _quality_metrics(frame, mapping)
    warnings.extend(q_warnings)
    top_defects: list[dict[str, Any]] = []
    if mapping["defect_type"]["column"]:
        top_defects = _top_defects(frame, mapping, max(1, top_n))
    group_analysis = _group_analysis(frame, mapping, max(1, top_n))
    period_analysis = None
    change_detection = None
    if mapping["date"]["column"]:
        period_analysis, p_warnings = _period_analysis(frame, mapping)
        warnings.extend(p_warnings)
        if period_analysis:
            change_detection = _change_detection(frame, mapping, period_analysis, group_analysis)
    return (
        {
            "quality_metrics": quality,
            "top_defects": top_defects,
            "group_analysis": group_analysis,
            "period_analysis": period_analysis,
            "change_detection": change_detection,
            "summary": {"row_count": int(len(frame))},
        },
        warnings,
    )


def _normalize_filter(row_filter: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(row_filter, dict):
        return None
    role = str(row_filter.get("role") or "").strip().lower()
    value = str(row_filter.get("value") or "").strip()
    if not value and role in {"equipment", "item", "process"}:
        return None
    if role not in {"equipment", "item", "process"}:
        for key in ("equipment", "item", "process"):
            if row_filter.get(key):
                role, value = key, str(row_filter.get(key) or "").strip()
                break
    if role not in {"equipment", "item", "process"} or not value:
        return None
    return {"role": role, "value": value}


def _norm_dim(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _match_dimension_value(series, wanted: str) -> tuple[str | None, list[str]]:
    uniques: list[str] = []
    seen: set[str] = set()
    for raw in series.dropna().astype(str):
        text = str(raw).strip()
        if not text or text.lower() == "nan" or text in seen:
            continue
        seen.add(text)
        uniques.append(text)
    wanted_n = _norm_dim(wanted)
    if not wanted_n:
        return None, uniques
    exact = [v for v in uniques if _norm_dim(v) == wanted_n]
    if exact:
        return exact[0], uniques
    suffix: list[str] = []
    for value in uniques:
        vn = _norm_dim(value)
        if vn.endswith(wanted_n) and (len(vn) == len(wanted_n) or not vn[: -len(wanted_n)][-1:].isdigit()):
            suffix.append(value)
        elif wanted_n.endswith(vn) and vn and (len(wanted_n) == len(vn) or not wanted_n[: -len(vn)][-1:].isdigit()):
            suffix.append(value)
    if len(suffix) == 1:
        return suffix[0], uniques
    return None, uniques


def _apply_row_filter(
    frame,
    mapping: dict[str, Any],
    row_filter: Mapping[str, Any] | None,
    top_n: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    spec = _normalize_filter(row_filter)
    if not spec:
        return None, None, []
    warnings: list[str] = []
    role = spec["role"]
    col = (mapping.get(role) or {}).get("column") if isinstance(mapping.get(role), dict) else None
    context = {
        "role": role,
        "value": spec["value"],
        "matched": False,
        "matched_value": None,
        "row_count": 0,
        "error": None,
        "candidates": [],
    }
    if not col:
        context["error"] = "missing_column"
        warnings.append(f"{role} 컬럼이 없어 '{spec['value']}'만 따로 분석하지 못했습니다.")
        return context, None, warnings
    matched, uniques = _match_dimension_value(frame[col], spec["value"])
    context["candidates"] = uniques[:20]
    if not matched:
        context["error"] = "no_match"
        warnings.append(f"'{spec['value']}'에 해당하는 {role} 행을 찾지 못했습니다.")
        return context, None, warnings
    subset = frame[frame[col].astype(str).str.strip().map(_norm_dim) == _norm_dim(matched)].copy()
    if subset.empty:
        context["error"] = "no_match"
        warnings.append(f"'{spec['value']}'에 해당하는 {role} 행을 찾지 못했습니다.")
        return context, None, warnings
    context["matched"] = True
    context["matched_value"] = matched
    context["row_count"] = int(len(subset))
    scoped, scoped_warnings = _run_metrics(subset, mapping, top_n)
    warnings.extend(scoped_warnings)
    return context, scoped, warnings


def _candidate_factors(metrics: Mapping[str, Any], *, filter_context: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    quality = metrics.get("quality_metrics") if isinstance(metrics.get("quality_metrics"), dict) else {}
    groups = metrics.get("group_analysis") if isinstance(metrics.get("group_analysis"), dict) else {}
    baseline = quality.get("defect_rate_pct")
    total_defects = quality.get("defect_qty_total")
    skip_role = (filter_context or {}).get("role") if isinstance(filter_context, Mapping) else None
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(item: dict[str, Any]) -> None:
        key = (str(item.get("role") or ""), str(item.get("key") or ""))
        if not key[1] or key in seen:
            return
        seen.add(key)
        found.append(item)

    for role, bucket in (("item", "by_item"), ("process", "by_process"), ("equipment", "by_equipment")):
        if role == skip_role:
            continue
        rows = list(groups.get(bucket) or [])
        if not rows:
            continue
        top = rows[0]
        rate = top.get("defect_rate_pct")
        notable_rate = False
        if rate is not None and baseline is not None:
            notable_rate = rate >= baseline + 1 or (baseline > 0 and rate >= baseline * 1.3)
        share = None
        if total_defects and top.get("defect_qty"):
            share = top["defect_qty"] / total_defects * 100
        if notable_rate or (share is not None and share >= 30):
            add(
                {
                    "kind": "group_rate" if notable_rate else "group_share",
                    "role": role,
                    "key": top.get("key"),
                    "defect_rate_pct": rate,
                    "defect_qty": top.get("defect_qty"),
                    "share_pct": _json_num(share) if share is not None else None,
                    "baseline_rate_pct": baseline,
                    "evidence": f"group_analysis.{bucket}",
                }
            )

    defects = list(metrics.get("top_defects") or [])
    if defects:
        top = defects[0]
        qty = top.get("defect_qty", top.get("count"))
        share = (qty / total_defects * 100) if total_defects and qty else None
        if share is not None and share >= 30:
            add(
                {
                    "kind": "defect_share",
                    "role": "defect_type",
                    "key": top.get("type"),
                    "defect_qty": qty,
                    "share_pct": _json_num(share),
                    "evidence": "top_defects",
                }
            )

    change = metrics.get("change_detection") if isinstance(metrics.get("change_detection"), dict) else {}
    for spike in list(change.get("spikes") or [])[:2]:
        if spike.get("group_role") == skip_role:
            continue
        add(
            {
                "kind": "spike",
                "role": spike.get("group_role"),
                "key": spike.get("key"),
                "defect_rate_pct": spike.get("defect_rate_pct"),
                "baseline_rate_pct": spike.get("baseline_rate_pct"),
                "evidence": "change_detection.spikes",
            }
        )
    for conc in list(change.get("concentration") or [])[:2]:
        if conc.get("group_role") == skip_role:
            continue
        add(
            {
                "kind": "concentration",
                "role": conc.get("group_role"),
                "key": conc.get("key"),
                "share_pct": conc.get("share_pct"),
                "evidence": "change_detection.concentration",
            }
        )
    return found[:4]


def _additional_data_suggestions(columns: list[Any], filter_role: str | None) -> list[str]:
    blob = " ".join(str(c) for c in (columns or [])).lower()
    catalog = {
        "equipment": [
            ("설비 온도", ("온도", "temp", "temperature")),
            ("진동", ("진동", "vibration", "vibrat")),
            ("Cycle Time", ("cycle", "사이클", "tact", "택트")),
            ("정비 이력", ("정비", "유지보수", "maint")),
        ],
        "process": [
            ("Cycle Time", ("cycle", "사이클", "tact", "택트")),
            ("압력", ("압력", "pressure")),
            ("속도/설정값", ("속도", "speed", "설정")),
            ("작업자/교대", ("작업자", "교대", "shift")),
        ],
        "item": [
            ("원자재 LOT", ("lot", "로트", "배치")),
            ("공급업체", ("공급", "vendor", "업체")),
            ("작업자/교대", ("작업자", "교대", "shift")),
            ("검사 조건", ("검사조건", "측정조건")),
        ],
    }
    default = catalog["equipment"][:2] + catalog["process"][:1] + catalog["item"][:1]
    rows = catalog.get(str(filter_role or ""), default)
    suggestions: list[str] = []
    for label, tokens in rows:
        if any(token.lower() in blob for token in tokens):
            continue
        suggestions.append(label)
        if len(suggestions) >= 4:
            break
    return suggestions


def _top_defects(frame, mapping: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    col = mapping["defect_type"]["column"]
    if not col:
        return []
    defect_col = _qty_mapped_column(mapping, "defect_qty")
    work = frame[[col]].copy()
    work[col] = work[col].astype(str).str.strip()
    work = work[work[col].notna() & (work[col] != "") & (work[col].str.lower() != "nan")]
    if defect_col:
        work["__qty"] = _to_numeric(frame[defect_col]).fillna(0)
        grouped = work.groupby(col, dropna=False)["__qty"].sum().sort_values(ascending=False)
        return [{"type": str(idx), "defect_qty": _json_num(val)} for idx, val in grouped.head(top_n).items()]
    counts = work[col].value_counts().head(top_n)
    return [{"type": str(idx), "count": int(val)} for idx, val in counts.items()]


def _group_analysis(frame, mapping: dict[str, Any], top_n: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for role, key in (("item", "by_item"), ("process", "by_process"), ("equipment", "by_equipment")):
        col = mapping[role]["column"]
        if not col:
            continue
        out[key] = _aggregate_groups(frame, col, mapping, top_n)
    return out


def _aggregate_groups(frame, group_col: str, mapping: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    prod_col = _qty_mapped_column(mapping, "production_qty")
    defect_col = _qty_mapped_column(mapping, "defect_qty")
    result_col = mapping["result"]["column"]
    work = frame.copy()
    work["__group"] = work[group_col].astype(str).str.strip()
    work = work[work["__group"].notna() & (work["__group"] != "") & (work["__group"].str.lower() != "nan")]
    if work.empty:
        return []

    rows = []
    grouped = work.groupby("__group", dropna=False)
    for name, part in grouped:
        item = {"key": str(name), "rows": int(len(part))}
        if prod_col:
            item["production_qty"] = _json_num(_to_numeric(part[prod_col]).fillna(0).sum())
        if defect_col:
            item["defect_qty"] = _json_num(_to_numeric(part[defect_col]).fillna(0).sum())
        if item.get("production_qty") and item.get("defect_qty") is not None and item["production_qty"] > 0:
            item["defect_rate_pct"] = _json_num(item["defect_qty"] / item["production_qty"] * 100)
        if result_col:
            _ok, ng, _unk = _result_mask(part[result_col])
            item["ng_count"] = int(ng.sum())
            if len(part):
                item["ng_rate_pct"] = _json_num(item["ng_count"] / len(part) * 100)
        rows.append(item)

    def sort_key(row: dict[str, Any]) -> tuple:
        return (
            -(row.get("defect_qty") or row.get("ng_count") or 0),
            -(row.get("defect_rate_pct") or row.get("ng_rate_pct") or 0),
            -row.get("rows", 0),
        )

    rows.sort(key=sort_key)
    return rows[: max(1, top_n)]


def _parse_dates(series):
    import pandas as pd

    if series is None:
        return pd.Series(dtype="datetime64[ns]"), 0, 0
    native = pd.to_datetime(series, errors="coerce")
    if native.notna().mean() >= 0.6:
        parsed = native
    else:
        parsed = pd.to_datetime(series.astype(str).str.strip(), errors="coerce", format="mixed")
    total = int(series.notna().sum())
    ok = int(parsed.notna().sum())
    mixed = 0
    if total:
        ratio = ok / total
        if 0.2 <= ratio < 0.95:
            mixed = total - ok
    return parsed, ok, mixed


def _period_analysis(frame, mapping: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    import pandas as pd

    warnings: list[str] = []
    col = mapping["date"]["column"]
    parsed, ok, mixed = _parse_dates(frame[col])
    if mixed:
        warnings.append(f"날짜 컬럼 '{col}'의 형식이 섞여 있어 {mixed}건은 기간 집계에서 제외했습니다.")
    if ok < 2:
        warnings.append("기간 분석에 쓸 수 있는 날짜가 부족합니다.")
        return None, warnings

    work = frame.copy()
    work["__date"] = parsed
    work = work.dropna(subset=["__date"])
    prod_col = _qty_mapped_column(mapping, "production_qty")
    defect_col = _qty_mapped_column(mapping, "defect_qty")

    def bucket(freq: str, label: str, limit: int) -> list[dict[str, Any]]:
        grouped = work.groupby(pd.Grouper(key="__date", freq=freq))
        rows = []
        for ts, part in grouped:
            if part.empty:
                continue
            item = {"period": _period_label(ts, label), "rows": int(len(part))}
            if prod_col:
                item["production_qty"] = _json_num(_to_numeric(part[prod_col]).fillna(0).sum())
            if defect_col:
                item["defect_qty"] = _json_num(_to_numeric(part[defect_col]).fillna(0).sum())
            if item.get("production_qty") and item.get("defect_qty") is not None and item["production_qty"] > 0:
                item["defect_rate_pct"] = _json_num(item["defect_qty"] / item["production_qty"] * 100)
            rows.append(item)
        return rows[-limit:]

    return (
        {
            "date_column": col,
            "daily": bucket("D", "day", 31),
            "weekly": bucket("W-MON", "week", 12),
            "monthly": bucket("MS", "month", 12),
        },
        warnings,
    )


def _period_label(ts, grain: str) -> str:
    if grain == "day":
        return ts.strftime("%Y-%m-%d")
    if grain == "week":
        return ts.strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m")


def _change_detection(frame, mapping, period_analysis: dict[str, Any], group_analysis: dict[str, Any]) -> dict[str, Any] | None:
    monthly = period_analysis.get("monthly") or []
    weekly = period_analysis.get("weekly") or []
    series = monthly if len(monthly) >= 2 else weekly
    grain = "month" if len(monthly) >= 2 else "week" if len(weekly) >= 2 else None
    if not grain or len(series) < 2:
        spikes = _spikes(group_analysis)
        concentration = _concentration(group_analysis)
        if not spikes and not concentration:
            return None
        return {"period_grain": None, "latest_period": None, "previous_period": None, "spikes": spikes, "concentration": concentration}

    latest = series[-1]
    prev = series[-2]
    out: dict[str, Any] = {
        "period_grain": grain,
        "latest_period": latest.get("period"),
        "previous_period": prev.get("period"),
        "rows_delta": latest.get("rows", 0) - prev.get("rows", 0),
        "spikes": _spikes(group_analysis),
        "concentration": _concentration(group_analysis),
    }
    if latest.get("defect_qty") is not None and prev.get("defect_qty") is not None:
        out["defect_qty_delta"] = _json_num(latest["defect_qty"] - prev["defect_qty"])
    if latest.get("defect_rate_pct") is not None and prev.get("defect_rate_pct") is not None:
        out["defect_rate_delta_pp"] = _json_num(latest["defect_rate_pct"] - prev["defect_rate_pct"])
    return out


def _spikes(group_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    spikes = []
    for key, role in (("by_item", "item"), ("by_process", "process"), ("by_equipment", "equipment")):
        rows = group_analysis.get(key) or []
        rates = [r.get("defect_rate_pct") for r in rows if r.get("defect_rate_pct") is not None]
        if len(rates) < 2:
            continue
        overall = sum(rates) / len(rates)
        for row in rows:
            rate = row.get("defect_rate_pct")
            if rate is None:
                continue
            if rate >= max(overall * 2, overall + 5) and (row.get("rows") or 0) >= 2:
                spikes.append(
                    {
                        "group_role": role,
                        "key": row["key"],
                        "defect_rate_pct": rate,
                        "baseline_rate_pct": _json_num(overall),
                    }
                )
    return spikes[:8]


def _concentration(group_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for key, role in (("by_item", "item"), ("by_process", "process"), ("by_equipment", "equipment")):
        rows = group_analysis.get(key) or []
        total = sum((r.get("defect_qty") or 0) for r in rows)
        if total <= 0 or not rows:
            continue
        top = max(rows, key=lambda r: r.get("defect_qty") or 0)
        share = (top.get("defect_qty") or 0) / total * 100
        if share >= 50:
            found.append(
                {
                    "group_role": role,
                    "key": top["key"],
                    "share_pct": _json_num(share),
                }
            )
    return found


def _detail_message(*, filename: str, schema: dict[str, Any], quality: dict[str, Any] | None, needs_clarification: bool, warnings: list[str]) -> str:
    if needs_clarification:
        return f"{filename} 구조를 확인했지만 추가 확인이 필요합니다."
    if quality and quality.get("defect_rate_pct") is not None:
        return (
            f"{filename} 품질 분석 완료: 생산 {quality.get('production_qty_total')} / "
            f"불량 {quality.get('defect_qty_total')} / 불량률 {quality.get('defect_rate_pct')}%"
        )
    if quality and quality.get("ng_count") is not None:
        return f"{filename} 판정 집계 완료: NG {quality.get('ng_count')}건 / 검사 {quality.get('inspected_rows')}건"
    return f"{filename} 데이터 구조 분석 완료: {schema.get('row_count')}행 {schema.get('column_count')}열"


def _json_num(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
    except TypeError:
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 4)


def _fail(
    detail: str,
    *,
    error: str,
    file: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    needs_clarification: bool = False,
    clarification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "file": file,
        "schema": schema,
        "column_mapping": {},
        "summary": None,
        "quality_metrics": None,
        "top_defects": [],
        "group_analysis": {},
        "period_analysis": None,
        "change_detection": None,
        "stale_overrides": [],
        "warnings": [detail],
        "needs_clarification": needs_clarification,
        "clarification": clarification,
        "detail": detail,
        "error": error,
    }
