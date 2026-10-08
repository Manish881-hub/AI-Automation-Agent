from typing import Literal
from pydantic import BaseModel, Field, HttpUrl


class TestRunRequest(BaseModel):
    url: HttpUrl
    objective: str = Field(min_length=5, max_length=2000)


class TestStep(BaseModel):
    id: int
    action: Literal["navigate", "click", "fill", "press", "assert"]
    target: str = ""
    value: str = ""
    expected: str = ""
    assertion_type: Literal["text", "visible", "url", "title", "element", "no_console_error", "http_success"] | None = None
    reason: str = ""


class TestPlan(BaseModel):
    steps: list[TestStep]


class TestEvidence(BaseModel):
    url: str = ""
    title: str = ""
    text: str = ""
    console_errors: list[str] = []
    network_errors: list[str] = []
    screenshot: str | None = None


class StepResult(BaseModel):
    step_id: int
    status: Literal["passed", "failed", "error"]
    message: str
    evidence: TestEvidence | None = None
    retried: bool = False


class SnapshotLink(BaseModel):
    text: str = ""
    href: str = ""


class SnapshotInput(BaseModel):
    label: str = ""
    name: str = ""
    type: str = "text"
    placeholder: str = ""


class SnapshotForm(BaseModel):
    action: str = ""
    fields: list[str] = []


class WebsiteSnapshot(BaseModel):
    """Compact, interaction-focused page model from reconnaissance.

    Deliberately NOT the DOM: only the targets a planner needs —
    headings for orientation, buttons/links/inputs/forms for actions,
    plus truncated visible text. Keeps LLM context small and plans grounded.
    """

    url: str = ""
    title: str = ""
    headings: list[str] = []
    links: list[SnapshotLink] = []
    buttons: list[str] = []
    inputs: list[SnapshotInput] = []
    forms: list[SnapshotForm] = []
    visible_text: str = ""
    screenshot: str | None = None


class RecoveryDecision(BaseModel):
    action: Literal["retry_alternate_target", "skip", "abort"]
    target: str = ""
    reason: str = ""


class FailureAnalysis(BaseModel):
    failed: bool
    summary: str
    probable_root_cause: str
    evidence: list[str] = []
    recommended_actions: list[str] = []


class TestRunResponse(BaseModel):
    run_id: str
    status: str
    objective: str
    url: str
    plan: TestPlan | None = None
    results: list[StepResult] = []
    analysis: FailureAnalysis | None = None
    report: str | None = None
    website: WebsiteSnapshot | None = None
