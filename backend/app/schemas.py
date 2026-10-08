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
