import re

from ..schemas import RecoveryDecision, TestStep
from ..services.llm import LLM


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[→⇒›»«\"'()\\[\\]{}:;!?.]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# Deterministic semantic equivalents: recovery without any LLM call.
# Lookup is bidirectional — each tuple is one meaning, ordered so
# alternates are tried in a stable, reviewable sequence.
SYNONYM_GROUPS = [
    ("sign in", "log in", "login", "log-in"),
    ("signin", "login"),
    ("create account", "sign up", "signup", "register"),
    ("forgot password", "forgot password?", "reset password"),
    ("submit", "continue", "next"),
    ("search", "find"),
]


def _synonym_alternates(want: str) -> list[str]:
    for group in SYNONYM_GROUPS:
        if want in group:
            return [g for g in group if g != want]
    return []


def suggest(step: TestStep, available: dict) -> RecoveryDecision | None:
    """Deterministic semantic-equivalent lookup — no LLM call.

    Tries exact match, then containment, then token overlap against the
    live options for this action. Returns None when nothing is close,
    letting the caller fall back to the LLM or give up cleanly.
    """
    if step.action == "click" or step.action == "press":
        pool = list(available.get("buttons", [])) + list(available.get("links", []))
    elif step.action == "fill":
        pool = list(available.get("inputs", []))
    else:
        return None

    want = _normalize(step.target)
    if not want:
        return None
    normed = [(c, _normalize(c)) for c in pool if c and _normalize(c)]
    # 1. exact (case/punctuation-insensitive): "Sign in" == "Sign In →"
    for original, n in normed:
        if n == want:
            return RecoveryDecision(
                action="retry_alternate_target",
                target=original,
                reason=f"exact match for {step.target!r}",
            )
    # 2. containment either way: "Sign in" ~ "Sign in with SSO"
    for original, n in normed:
        if want in n or n in want:
            return RecoveryDecision(
                action="retry_alternate_target",
                target=original,
                reason=f"closest visible option for {step.target!r}",
            )
    # 3. deterministic synonyms: "Sign in" ~ "Log in" regardless of model.
    by_norm = {n: original for original, n in normed}
    for alt in _synonym_alternates(want):
        if alt in by_norm:
            return RecoveryDecision(
                action="retry_alternate_target",
                target=by_norm[alt],
                reason=f"synonym of {step.target!r}",
            )
        for n, original in by_norm.items():
            if alt in n or n in alt:
                return RecoveryDecision(
                    action="retry_alternate_target",
                    target=original,
                    reason=f"synonym of {step.target!r}",
                )
    # 4. token overlap: "Create account" ~ "Create new account" matches.
    want_tokens = set(want.split())
    best: tuple[str, int] | None = None
    for original, n in normed:
        overlap = len(want_tokens & set(n.split()))
        if overlap and (best is None or overlap > best[1]):
            best = (original, overlap)
    if best and best[1] >= max(1, len(want_tokens) // 2):
        return RecoveryDecision(
            action="retry_alternate_target",
            target=best[0],
            reason=f"semantic equivalent of {step.target!r}",
        )
    return None


class RecoveryAgent:
    """Turn a step failure into a second chance, not a run failure.

    Policy: deterministic heuristic first (free, instant, testable),
    LLM judgment only when the page offers no obvious equivalent,
    safe abort default when the LLM itself is unavailable.
    """

    name = "recovery"

    async def run(
        self,
        llm: LLM,
        step: TestStep,
        available: dict,
        error: str,
    ) -> RecoveryDecision:
        heuristic = suggest(step, available)
        if heuristic is not None:
            return heuristic

        options = (
            list(available.get("buttons", []))
            + list(available.get("links", []))
            + list(available.get("inputs", []))
        )[:30]
        system = """You are a recovery strategist for a web-test agent. A planned action failed because its target was not found.
Decide: retry_alternate_target (with the closest visible target), skip (step is non-essential), or abort (plan is invalid).
Return JSON matching the requested schema. Never invent targets — only use the available options, or skip/abort."""
        user = (
            f"Failed step: {step.action} target={step.target!r} error={error}\n"
            f"Available options: {options}"
        )
        try:
            return await llm.structured(system, user, RecoveryDecision)
        except Exception as exc:
            return RecoveryDecision(action="abort", reason=f"recovery unavailable: {exc}")
