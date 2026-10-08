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
    for blob in blobs:
        for tok in _TOKEN_RE.findall((blob or "").lower()):
            if tok not in terms:
                terms.append(tok)
    terms.sort(key=len, reverse=True)
    return terms[:6]


class CodebaseAgent:
    """Locate the code behind a failure: search, then read the top hits."""

    name = "codebase"

    def investigate(
        self,
        tool: CodebaseTool,
        results: list[StepResult],
        analysis: FailureAnalysis | None,
        max_files: int = 3,
    ) -> dict:
        terms = failure_terms(results, analysis)
        hits: list[dict] = []
        for term in terms[:3]:
            hits.extend(tool.search(re.escape(term), limit=10))
            if len(hits) >= 15:
                break
        seen: list[str] = []
        for h in hits:
            if h["file"] not in seen:
                seen.append(h["file"])
        contents = {}
        for rel in seen[:max_files]:
            try:
                contents[rel] = tool.read_file(rel)
            except ValueError:
                continue
        return {"terms": terms, "hits": hits[:15], "files": contents}
