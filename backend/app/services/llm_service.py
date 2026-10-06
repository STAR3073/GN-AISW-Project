from collections.abc import Iterator
from typing import Protocol, TypeVar
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import get_settings
from app.schemas.agent import (
    ActionExtraction,
    ActionItem,
    ClarificationChoice,
    DocumentUnderstanding,
    KeyDate,
    PlannedTask,
    SemanticIntent,
    TaskPlan,
    ToolCall,
    ToolSelection,
)

T = TypeVar("T", bound=BaseModel)


def _chunk_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(getattr(block, "text", "") or ""))
        return "".join(parts)
    return str(content)


class LLMProvider(Protocol):
    def chat(self, messages: list[dict], *, model: str | None = None) -> str: ...

    def stream_chat(self, messages: list[dict], *, model: str | None = None) -> Iterator[str]: ...

    def structured(
        self,
        messages: list[dict],
        schema: type[T],
        *,
        model: str | None = None,
    ) -> T: ...


def _to_lc_messages(messages: list[dict]):
    converted = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            converted.append(SystemMessage(content=content))
        else:
            converted.append(HumanMessage(content=content))
    return converted


class OpenAIProvider:
    def chat(self, messages: list[dict], *, model: str | None = None) -> str:
        settings = get_settings()
        llm = ChatOpenAI(
            model=model or settings.llm_plan_model,
            api_key=settings.openai_api_key,
            temperature=0.2,
        )
        result = llm.invoke(_to_lc_messages(messages))
        return str(result.content)

    def stream_chat(self, messages: list[dict], *, model: str | None = None) -> Iterator[str]:
        settings = get_settings()
        llm = ChatOpenAI(
            model=model or settings.llm_plan_model,
            api_key=settings.openai_api_key,
            temperature=0.2,
            streaming=True,
        )
        for chunk in llm.stream(_to_lc_messages(messages)):
            text = _chunk_text(chunk.content)
            if text:
                yield text

    def structured(
        self,
        messages: list[dict],
        schema: type[T],
        *,
        model: str | None = None,
    ) -> T:
        settings = get_settings()
        llm = ChatOpenAI(
            model=model or settings.llm_extract_model,
            api_key=settings.openai_api_key,
            temperature=0,
        )
        bound = llm.with_structured_output(schema)
        return bound.invoke(_to_lc_messages(messages))


class MockLLMProvider:
    """API 키 없이 파이프라인/UI를 검증하기 위한 고정 응답."""

    def chat(self, messages: list[dict], *, model: str | None = None) -> str:
        blob = " ".join(str(m.get("content") or "") for m in messages)
        if "짧은 한국어 제목" in blob or "제목만 출력" in blob:
            user_line = ""
            for line in blob.splitlines():
                if line.startswith("사용자:"):
                    user_line = line.split(":", 1)[-1].strip()
                    break
            if "비교" in user_line:
                return "공고문·사업계획서 비교"
            if "PDF" in user_line or "변환" in user_line:
                return "HWP PDF 변환"
            if "성과" in user_line or "지표" in user_line:
                return "사업계획서 성과지표 분석"
            compact = " ".join(user_line.split())
            return compact[:18] or "업무 문서 분석"
        user = messages[-1]["content"] if messages else ""
        if "EXCEL_JSON:" in blob:
            from app.agent.excel_reply import format_excel_reply

            raw = blob.split("EXCEL_JSON:", 1)[-1].strip()
            if raw and raw != "null":
                try:
                    data = json.loads(raw)
                    explained = format_excel_reply(data)
                    if explained:
                        return explained
                except Exception:
                    pass
        if "초안" in user or "generate_draft" in user:
            return "요청하신 문서 초안을 생성해 파일로 저장했습니다. Action 상태가 DRAFT_COMPLETED로 바뀌었습니다."
        if "남은" in user or "아직" in user:
            return (
                "아직 끝나지 않은 업무입니다.\n"
                "- [P1] AI Agent MVP 개발 (마감: 제출일)\n"
                "- [P1] 완료보고서 작성\n"
                "- [P2] 시연영상 제작\n"
                "가장 먼저 MVP 개발을 진행하는 것이 좋습니다."
            )
        return (
            "문서를 분석해 Action Item을 추출하고, 마감일 기준으로 업무를 분해한 뒤 "
            "Task와 Mock Calendar 일정을 생성했습니다. 우측 패널에서 상태를 확인할 수 있습니다."
        )

    def stream_chat(self, messages: list[dict], *, model: str | None = None) -> Iterator[str]:
        text = self.chat(messages, model=model)
        for i in range(0, len(text), 2):
            yield text[i : i + 2]

    def structured(
        self,
        messages: list[dict],
        schema: type[T],
        *,
        model: str | None = None,
    ) -> T:
        if schema is DocumentUnderstanding:
            return DocumentUnderstanding(
                doc_type="공고문",
                purpose="경남 AI·SW 경진대회 참가 및 산출물 제출",
                key_dates=[
                    KeyDate(label="참가 신청", date="2026-10-10"),
                    KeyDate(label="최종 제출", date="2026-10-20"),
                ],
                requirements=[
                    "AI Agent MVP 구현",
                    "시연이 가능한 완성도",
                    "제출 서류 구비",
                ],
                deliverables=[
                    "소스코드",
                    "완료보고서",
                    "기술설명서",
                    "시연영상",
                    "발표자료",
                ],
                summary="문서는 경진대회 참가 공고로, 기한 내 MVP와 제출물을 완성해야 한다.",
            )
        if schema is ActionExtraction:
            return ActionExtraction(
                actions=[
                    ActionItem(
                        title="AI Agent MVP 개발",
                        description="Document → Action → Execution 파이프라인을 동작하는 MVP로 구현한다.",
                        deadline="2026-10-18",
                        deliverable="동작하는 소스코드",
                        priority="P1",
                        source="공고문 과제 요구사항",
                    ),
                    ActionItem(
                        title="완료보고서 작성",
                        description="개발 과정과 결과를 정리한 완료보고서를 제출한다.",
                        deadline="2026-10-20",
                        deliverable="완료보고서",
                        priority="P1",
                        source="제출 서류 목록",
                    ),
                    ActionItem(
                        title="기술설명서 작성",
                        description="아키텍처와 사용 기술을 설명하는 문서를 작성한다.",
                        deadline="2026-10-20",
                        deliverable="기술설명서",
                        priority="P1",
                        source="제출 서류 목록",
                    ),
                    ActionItem(
                        title="소스코드 정리",
                        description="실행 방법과 구조를 파악할 수 있게 저장소를 정리한다.",
                        deadline="2026-10-19",
                        deliverable="소스코드 패키지",
                        priority="P1",
                        source="제출 서류 목록",
                    ),
                    ActionItem(
                        title="시연영상 제작",
                        description="핵심 시나리오가 보이도록 시연 영상을 찍는다.",
                        deadline="2026-10-19",
                        deliverable="시연영상",
                        priority="P2",
                        source="제출 서류 목록",
                    ),
                    ActionItem(
                        title="발표자료 제작",
                        description="심사 발표용 슬라이드를 만든다.",
                        deadline="2026-10-20",
                        deliverable="발표자료",
                        priority="P2",
                        source="제출 서류 목록",
                    ),
                ]
            )
        if schema is TaskPlan:
            return TaskPlan(
                final_deadline="2026-10-20",
                tasks=[
                    PlannedTask(action_title="AI Agent MVP 개발", title="문서 파서 구현", estimated_hours=4),
                    PlannedTask(action_title="AI Agent MVP 개발", title="LangGraph 워크플로 연결", estimated_hours=8),
                    PlannedTask(action_title="AI Agent MVP 개발", title="대시보드 E2E 점검", estimated_hours=4),
                    PlannedTask(action_title="완료보고서 작성", title="완료보고서 목차/초안", estimated_hours=3),
                    PlannedTask(action_title="기술설명서 작성", title="아키텍처 설명 초안", estimated_hours=3),
                    PlannedTask(action_title="소스코드 정리", title="README와 실행 스크립트 정리", estimated_hours=2),
                    PlannedTask(action_title="시연영상 제작", title="데모 시나리오 리허설 촬영", estimated_hours=4),
                    PlannedTask(action_title="발표자료 제작", title="발표 슬라이드 초안", estimated_hours=3),
                ],
            )
        if schema is ToolSelection:
            user = " ".join(m.get("content", "") for m in messages)
            if any(k in user.lower() for k in ("불량", "엑셀", "excel", "xlsx", "csv", "품질 현황", "analyze_excel", "tabular_data_analysis")):
                return ToolSelection(
                    tool_calls=[ToolCall(tool="analyze_excel", args={})],
                    rationale="Excel/CSV 품질 데이터 분석을 수행한다.",
                )
            if "초안" in user or "보고서" in user:
                return ToolSelection(
                    tool_calls=[
                        ToolCall(tool="generate_draft", args={"title": "완료보고서 초안"}),
                    ],
                    rationale="사용자 요청에 따라 문서 초안을 생성한다.",
                )
            return ToolSelection(
                tool_calls=[
                    ToolCall(tool="create_tasks", args={}),
                    ToolCall(tool="create_calendar_events", args={}),
                ],
                rationale="추출된 Action을 Task와 Calendar로 반영한다.",
            )
        if schema is SemanticIntent:
            blob = " ".join(m.get("content", "") for m in messages)
            has_tabular = '"has_tabular": true' in blob or '"has_tabular":true' in blob
            tabular_count = 0
            if '"tabular_count":' in blob:
                try:
                    after = blob.split('"tabular_count":', 1)[1].split(",", 1)[0].strip()
                    tabular_count = int(after)
                except Exception:
                    tabular_count = 1 if has_tabular else 0
            if has_tabular and tabular_count > 1:
                return SemanticIntent(
                    intent="execute",
                    goal="첨부된 제조 데이터 확인",
                    requires_tool=False,
                    suggested_capability="tabular_data_analysis",
                    confidence=0.72,
                    reason="Excel/CSV가 여러 개라 대상을 확인해야 함",
                    clarification="현재 Excel/CSV 파일이 여러 개입니다. 어떤 파일을 확인할까요?",
                )
            if has_tabular:
                return SemanticIntent(
                    intent="execute",
                    goal="현재 세션의 제조 데이터 상태 확인",
                    requires_tool=True,
                    suggested_capability="tabular_data_analysis",
                    confidence=0.88,
                    reason="세션에 제조 Excel/CSV가 있고 사용자가 상태/이상을 확인해 달라고 함",
                )
            if '"has_prose_document": true' in blob:
                return SemanticIntent(
                    intent="ingest",
                    goal="첨부 문서 확인",
                    requires_tool=False,
                    suggested_capability="document_ingest",
                    confidence=0.7,
                    reason="본문 문서가 선택되어 있음",
                )
            return SemanticIntent(
                intent="query",
                goal="",
                requires_tool=False,
                suggested_capability="none",
                confidence=0.55,
                reason="대상 자료가 불명확함",
                clarification="어떤 내용을 확인해드릴까요?",
            )
        if schema is ClarificationChoice:
            blob = " ".join(m.get("content", "") for m in messages)
            if "코드" in blob and "품목코드" in blob:
                return ClarificationChoice(selected_index=1, confidence=0.93, reason="코드 선택")
            return ClarificationChoice(selected_index=None, confidence=0.2, reason="후보를 확정할 수 없음")
        return schema.model_validate({})


_provider: LLMProvider | None = None


def get_llm() -> LLMProvider:
    global _provider
    if _provider is None:
        settings = get_settings()
        if settings.llm_mock or not settings.openai_api_key:
            _provider = MockLLMProvider()
        else:
            _provider = OpenAIProvider()
    return _provider


def reset_llm() -> None:
    global _provider
    _provider = None
