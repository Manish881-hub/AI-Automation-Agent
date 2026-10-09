import { describe, expect, it } from "vitest";
import { getChecklist, getResultsPanel, type RunView } from "./runSummary";

const FAILURE_MSG =
  "LLM provider rate limit (HTTP 429): the model backend is temporarily out of shared capacity";

function failedPlanningRun(): RunView {
  return {
    status: "failed",
    plan: null,
    results: [],
    analysis: null,
    report: null,
    website: { title: "Jobs", buttons: ["Search"], inputs: [], links: [] },
    error_stage: "planning",
    error_kind: "infrastructure",
    error_message: FAILURE_MSG,
    objective_status: "unknown",
  };
}

describe("getResultsPanel", () => {
  it("keeps waiting while the run is active", () => {
    expect(getResultsPanel({ status: "running", results: [] })).toEqual({ kind: "waiting" });
  });

  it("shows results once steps exist, even on failure", () => {
    const panel = getResultsPanel({
      status: "failed",
      results: [{ step_id: 1, status: "failed", message: "nope" }],
    });
    expect(panel).toEqual({ kind: "results" });
  });

  it("says execution never started with the reason on terminal planning failure", () => {
    const panel = getResultsPanel(failedPlanningRun());
    expect(panel.kind).toBe("empty");
    if (panel.kind !== "empty") throw new Error("unreachable");
    expect(panel.headline).toBe("Test execution not started");
    expect(panel.detail).toContain(FAILURE_MSG);
    expect(panel.detail).toContain("planning");
  });

  it("distinguishes a plan that produced nothing", () => {
    const panel = getResultsPanel({
      status: "completed",
      plan: { steps: [{ id: 1, action: "navigate", target: "x" }] },
      results: [],
    });
    expect(panel.kind).toBe("empty");
    if (panel.kind !== "empty") throw new Error("unreachable");
    expect(panel.headline).toBe("Test execution produced no results");
  });
});

describe("getChecklist", () => {
  it("leaves no waiting pipeline items on a terminal planning failure", () => {
    const events = [
      "orchestrator: started x",
      "reconnaissance: website_understood 1 buttons",
      "planner: tool_call generate test plan",
      "orchestrator: run_failed planning: rate limited",
      "orchestrator: fatal_error rate limited",
    ];
    const states = new Map(getChecklist(failedPlanningRun(), events).map((i) => [i.id, i]));
    expect(states.get("recon")?.state).toBe("done");
    expect(states.get("plan")).toEqual(
      expect.objectContaining({ state: "fail", detail: "no plan generated" }),
    );
    expect(states.get("debug")?.state).toBe("skipped");
    expect(states.get("report")?.state).toBe("skipped");
    for (const item of states.values()) {
      expect(item.state).not.toBe("pending");
    }
  });

  it("settles debugger/report on a failed run that has a plan but no results", () => {
    const run: RunView = {
      status: "failed",
      plan: { steps: [{ id: 1, action: "navigate", target: "x" }] },
      results: [],
      analysis: null,
      report: null,
      website: { title: "Acme Portal" },
      error_stage: "planning",
      error_message: "plan rejected",
    };
    const states = new Map(getChecklist(run, []).map((i) => [i.id, i]));
    expect(states.get("debug")?.state).toBe("skipped");
    expect(states.get("report")?.state).toBe("skipped");
  });

  it("keeps the fix approval gate actionable when awaiting approval", () => {
    const run: RunView = {
      status: "completed",
      plan: { steps: [{ id: 1, action: "navigate", target: "x" }] },
      results: [{ step_id: 1, status: "passed", message: "ok" }],
      analysis: { failed: true },
      report: "r",
      fix_status: "awaiting_approval",
    };
    const states = new Map(getChecklist(run, []).map((i) => [i.id, i]));
    expect(states.get("fix")).toEqual(
      expect.objectContaining({ state: "pending", detail: "awaiting approval" }),
    );
  });
});
