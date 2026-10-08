from typing import Literal
from pydantic import AliasChoices, BaseModel, Field, HttpUrl, field_validator, model_validator


class TestRunRequest(BaseModel):
    url: HttpUrl
    objective: str = Field(min_length=5, max_length=2000)


class TestStep(BaseModel):
    # Small/cheap models rename keys (step/step_id) and emit nulls.
    # Accept those shapes here so a slightly-off reply still runs
    # instead of failing the whole run on schema pedantry.
    id: int = Field(validation_alias=AliasChoices("id", "step", "step_id"))
    action: Literal["navigate", "click", "fill", "press", "assert"]
    target: str = ""
    value: str = ""
    expected: str = ""
    assertion_type: Literal["text", "visible", "url", "title", "element", "no_console_error", "http_success"] | None = None
    reason: str = ""

    @field_validator("target", "value", "expected", "reason", mode="before")
    @classmethod
    def _coerce_to_str(cls, v):
        # Free models emit nulls and booleans in text fields.
        if v is None or isinstance(v, bool):
            return ""
        return v if isinstance(v, str) else str(v)


class TestPlan(BaseModel):
    steps: list[TestStep]

    @model_validator(mode="before")
    @classmethod
    def _unwrap_step_items(cls, data):
        # Accept [{"step": {...}}] as well as [{...}], a wrapping
        # {"test_plan" | "plan": {...}} envelope, or a bare steps list
        # stored under any key (cheap models rename "steps" to "plan").
        if isinstance(data, dict):
            if "steps" not in data:
                for key in ("test_plan", "plan", "testPlan"):
                    inner = data.get(key)
                    if isinstance(inner, dict):
                        data = inner
                        break
            if "steps" not in data:
                for value in data.values():
                    if (
                        isinstance(value, list)
                        and value
                        and isinstance(value[0], dict)
                        and "action" in value[0]
                    ):
                        data = {**data, "steps": value}
                        break
            steps = data.get("steps") if isinstance(data, dict) else None
            if isinstance(steps, list):
                fixed = []
                for position, item in enumerate(steps, 1):
                    if isinstance(item, dict) and "id" not in item and "step" in item:
                        inner = item["step"]
                        item = inner if isinstance(inner, dict) else item
                    if isinstance(item, dict) and "id" not in item and "step_id" not in item:
                        # No identifier at all: assign plan order.
                        item = {**item, "id": position}
                    fixed.append(item)
                data = {**data, "steps": fixed}
        return data


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
    recovered_from: str = ""


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


class PlanValidation(BaseModel):
    """Deterministic verdict on whether a plan tests the objective.

    success_step_ids names the assert steps that verify the business
    objective — the orchestrator derives objective_status from them,
    keeping step success and business success as separate concepts.
    """

    approved: bool
    reasons: list[str] = []
    success_step_ids: list[int] = []


class FailureAnalysis(BaseModel):
    failed: bool = True
    summary: str = ""
    probable_root_cause: str = ""
    evidence: list[str] = []
    recommended_actions: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _accept_model_synonyms(cls, data):
        # Cheap models use their own key names; map the common ones.
        if isinstance(data, dict):
            pick = lambda *keys: next(
                (data[k] for k in keys if data.get(k) not in (None, "")), None
            )
            failed = pick("failed", "is_failure", "has_failure", "failure_detected")
            summary = pick("summary", "analysis", "description", "finding", "conclusion")
            cause = pick(
                "probable_root_cause", "root_cause", "likely_cause", "cause",
                "probable_cause",
            )
            evidence = pick("evidence", "observations", "supporting_evidence")
            actions = pick(
                "recommended_actions", "recommendations", "next_steps",
                "actions", "suggested_actions",
            )
            data = dict(data)
            if failed is not None:
                data["failed"] = failed
            if summary is not None:
                data["summary"] = summary
            if cause is not None:
                data["probable_root_cause"] = cause
            if evidence is not None:
                data["evidence"] = evidence if isinstance(evidence, list) else [str(evidence)]
            if actions is not None:
                data["recommended_actions"] = actions if isinstance(actions, list) else [str(actions)]
        return data


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
    objective_status: str = "unknown"
    objective_reason: str = ""
