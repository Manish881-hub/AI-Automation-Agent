from typing import Literal
from pydantic import AliasChoices, BaseModel, Field, HttpUrl, field_validator, model_validator
import json
import re

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def normalize_url(url: str) -> str:
    """Strip chat/markdown wrapping to a raw URL. Applied at the validation
    boundary so internal state never holds anything but a raw URL — the
    agent never guesses between `http://x` and `[http://x](http://x)`."""
    url = url.strip()
    match = _MARKDOWN_LINK.fullmatch(url)
    if match:
        url = match.group(2).strip()
    return url


class TestRunRequest(BaseModel):
    url: HttpUrl
    objective: str = Field(min_length=5, max_length=2000)

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, v):
        return normalize_url(v) if isinstance(v, str) else v


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


class FixProposal(BaseModel):
    """LLM-proposed source fix: unified diff plus why and how to verify.

    Produced as text only. Applying it requires human approval through
    the fix gate; the diff is stored as fix.patch for review first.
    """

    diff: str = ""
    explanation: str = ""
    test_targets: list[str] = []


class Verification(BaseModel):
    """Verdict of the post-fix browser re-test on the original objective.

    pytest passing does not imply the customer workflow works — this
    replays the approved plan against the patched app and judges the
    same success assertions the objective verdict used.
    """

    verified: bool
    objective_status: str = "unknown"
    reason: str = ""
    steps_passed: int = 0
    steps_failed: int = 0


class PlanValidation(BaseModel):
    """Deterministic verdict on whether a plan tests the objective.

    success_step_ids names the assert steps that verify the business
    objective — the orchestrator derives objective_status from them,
    keeping step success and business success as separate concepts.
    """

    approved: bool
    reasons: list[str] = []
    success_step_ids: list[int] = []


def _as_text(v) -> str:
    """Coerce a model reply fragment to text (dicts become JSON, not crashes)."""
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def _as_list_of_text(v) -> list[str]:
    if isinstance(v, list):
        return [_as_text(i) or str(i) for i in v if i not in (None, "")]
    return [_as_text(v)]


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
                data["summary"] = _as_text(summary)
            if cause is not None:
                data["probable_root_cause"] = _as_text(cause)
            if evidence is not None:
                data["evidence"] = _as_list_of_text(evidence)
            if actions is not None:
                data["recommended_actions"] = _as_list_of_text(actions)
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
    fix_status: str = "none"
    fix_verify_summary: str = ""
    fix_explanation: str = ""
    fix_branch: str = ""
    fix_diff: str = ""
