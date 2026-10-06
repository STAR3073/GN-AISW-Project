from typing import Literal

from pydantic import BaseModel, Field


class KeyDate(BaseModel):
    label: str
    date: str = Field(description="YYYY-MM-DD, 모르면 빈 문자열")


class DocumentUnderstanding(BaseModel):
    doc_type: str
    purpose: str
    key_dates: list[KeyDate] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    summary: str


class ActionItem(BaseModel):
    title: str
    description: str
    deadline: str | None = Field(default=None, description="YYYY-MM-DD")
    deliverable: str = ""
    priority: Literal["P1", "P2", "P3"] = "P2"
    status: Literal["TODO", "IN_PROGRESS", "DRAFT_COMPLETED", "COMPLETED"] = "TODO"
    source: str = ""


class ActionExtraction(BaseModel):
    actions: list[ActionItem]


class PlannedTask(BaseModel):
    action_title: str
    title: str
    estimated_hours: float = 2.0
    notes: str = ""


class TaskPlan(BaseModel):
    tasks: list[PlannedTask]
    final_deadline: str | None = None


class ToolCall(BaseModel):
    tool: Literal["create_tasks", "create_calendar_events", "generate_draft", "analyze_excel", "generate_report"]
    args: dict = Field(default_factory=dict)


class ToolSelection(BaseModel):
    tool_calls: list[ToolCall]
    rationale: str = ""


class PlanStep(BaseModel):
    id: int = 1
    description: str = ""
    tool: str | None = None
    document_id: str = ""
    args: dict = Field(default_factory=dict)


class WorkPlan(BaseModel):
    goal: str = ""
    requires_tools: bool = False
    steps: list[PlanStep] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification: str = ""
    route: Literal["tools", "reply"] = "reply"
    pending: dict | None = None


class SemanticIntent(BaseModel):
    intent: Literal["query", "execute", "ingest"] = "query"
    goal: str = ""
    requires_tool: bool = False
    suggested_capability: Literal[
        "tabular_data_analysis",
        "document_ingest",
        "document_query",
        "draft",
        "none",
    ] = "none"
    confidence: float = Field(default=0.5, ge=0, le=1)
    reason: str = ""
    clarification: str = ""


class ClarificationChoice(BaseModel):
    selected_index: int | None = Field(default=None, description="0-based candidate index. 불확실하면 null")
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = ""


class EvaluationCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class EvaluationResult(BaseModel):
    status: Literal["pass", "retry", "clarify", "fail", "continue"] = "pass"
    goal_satisfied: bool = False
    checks: list[EvaluationCheck] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    retry_instruction: str = ""
    retry_target: Literal["planner", "tools", "none"] = "none"
    retry_args: dict = Field(default_factory=dict)
    user_message: str | None = None
    validated_facts: dict = Field(default_factory=dict)
