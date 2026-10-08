import re

from ..schemas import StepResult, FailureAnalysis
from ..tools.codebase import CodebaseTool

# Quoted identifiers ('transaction_id', "PaymentError") and dotted paths
# (/api/payment) carry the most search signal; bare CamelCase words help.
_QUOTED_RE = re.compile(r"['\"`]([\w./\-]+)['\"`]")
_TOKEN_RE = re.compile(r"\b[a-z_][a-z0-9_]{3,}\b")


def failure_terms(results: list[StepResult], analysis: FailureAnalysis | None) -> list[str]:
    """Deterministic search terms from observed failure evidence — no LLM.

    Collects quoted identifiers and snake_case tokens from step messages,
    console/network errors, and the debugger's stated cause, most specific
    (longest) first, capped so the search stays cheap.
    """
    blobs: list[str] = []
    for r in results:
        if r.status == "passed":
            continue
        blobs.append(r.message)
        if r.evidence:
            blobs.extend(r.evidence.console_errors)
            blobs.extend(r.evidence.network_errors)
    if analysis:
        blobs.append(analysis.probable_root_cause)
        blobs.extend(analysis.evidence)
    terms: list[str] = []
    for blob in blobs:
        for m in _QUOTED_RE.findall(blob or ""):
            if m not in terms:
                terms.append(m)
            # A quoted sentence rarely matches code verbatim, but its
            # words might ("Acme Portal — Sign in" -> sign).
            for w in re.split(r"\s+", m):
                w = w.strip(".,!?;:\"'()[]").lower()
                if len(w) > 3 and w not in terms:
                    terms.append(w)
    for blob in blobs:
        for tok in _TOKEN_RE.findall((blob or "").lower()):
            if tok not in terms:
                terms.append(tok)
    # Longest first (most specific), but every term gets tried — small
    # workspaces make exhaustive search cheaper than a missed file.
    terms.sort(key=len, reverse=True)
    return terms[:12]


class CodebaseAgent:
    """Locate the code behind a failure: search, then read the top hits."""

    name = "codebase"

    def investigate(
        self,
        tool: CodebaseTool,
        results: list[StepResult],
        analysis: FailureAnalysis | None,
        max_files: int = 4,
    ) -> dict:
        terms = failure_terms(results, analysis)
        hits: list[dict] = []
        for term in terms:
            hits.extend(tool.search(re.escape(term), limit=5))
            if len(hits) >= 15:
                break
        seen: list[str] = []
        for h in hits:
            if h["file"] not in seen:
                seen.append(h["file"])
        # Always pull the conventional test counterpart: the tests are the
        # executable specification the patch must satisfy.
        from pathlib import Path as _Path

        with_tests: list[str] = []
        for rel in seen:
            with_tests.append(rel)
            stem = _Path(rel).stem
            parent = _Path(rel).parent
            for candidate in (
                parent / "tests" / f"test_{stem}.py",
                parent.parent / "tests" / f"test_{stem}.py",
                _Path("tests") / f"test_{stem}.py",
            ):
                if str(candidate) not in with_tests and (tool.root / candidate).is_file():
                    with_tests.append(str(candidate))
                    break
        contents = {}
        for rel in with_tests[:max_files]:
            try:
                contents[rel] = tool.read_file(rel)
            except ValueError:
                continue
        return {"terms": terms, "hits": hits[:15], "files": contents}
