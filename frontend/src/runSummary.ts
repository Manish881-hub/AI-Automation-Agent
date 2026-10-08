// Pure view-model helpers for the demo panels. No React, no fetch —
// everything is derived from the polled run + events during render,
// so the checklist can never desync from server state.

export type SnapshotInput = {
  label?: string;
  name?: string;
  type?: string;
  placeholder?: string;
};

export type Website = {
  url?: string;
  title?: string;
  headings?: string[];
  links?: Array<string | { text?: string; href?: string }>;
  buttons?: string[];
  inputs?: SnapshotInput[];
  forms?: unknown[];
} | null | undefined;

export type PlanStep = {
  id: number;
  action: string;
  target?: string;
  expected?: string;
  reason?: string;
};

export type StepResultView = {
  step_id: number;
  status: "passed" | "failed" | "error";
  message?: string;
  retried?: boolean;
  recovered_from?: string;
};

export type RunView = {
  status: string;
  plan?: { steps?: PlanStep[] } | null;
  results?: StepResultView[] | null;
  analysis?: { failed?: boolean } | null;
  report?: string | null;
  website?: Website;
  fix_status?: string;
};

export type CheckState = "done" | "fail" | "pending";

export type CheckItem = {
  id: string;
  label: string;
  state: CheckState;
  detail?: string;
};

export function inputLabel(i: SnapshotInput): string {
  return i.label || i.placeholder || i.name || i.type || "input";
}

export function linkText(l: string | { text?: string; href?: string }): string {
  return typeof l === "string" ? l : l.text || l.href || "link";
}

function hasEvent(events: string[], needle: string): boolean {
  return events.some((e) => e.includes(needle));
}

function stepLabel(s: PlanStep): string {
  const what = s.action === "assert" ? s.expected || s.target || "assert" : s.target || s.action;
  return `${s.action} ${what}`.trim();
}

/** Agent execution checklist, derived during render from run + events. */
export function getChecklist(run: RunView, events: string[]): CheckItem[] {
  const items: CheckItem[] = [];
  const website = run.website ?? null;

  items.push(
    website
      ? { id: "recon", label: "reconnaissance", state: "done", detail: website.title || website.url }
      : hasEvent(events, "recon_failed")
        ? { id: "recon", label: "reconnaissance", state: "fail", detail: "snapshot unavailable" }
        : { id: "recon", label: "reconnaissance", state: "pending" },
  );

  const steps = run.plan?.steps ?? [];
  items.push(
    steps.length > 0
      ? { id: "plan", label: "planning", state: "done", detail: `${steps.length} steps` }
      : hasEvent(events, "fatal_error") || hasEvent(events, "timed_out")
        ? { id: "plan", label: "planning", state: "fail", detail: "no plan generated" }
        : { id: "plan", label: "planning", state: "pending" },
  );

  const byId = new Map((run.results ?? []).map((r) => [r.step_id, r]));
  for (const s of steps) {
    const r = byId.get(s.id);
    if (!r) {
      items.push({ id: `step-${s.id}`, label: stepLabel(s), state: "pending" });
    } else if (r.status === "passed") {
      items.push({
        id: `step-${s.id}`, label: stepLabel(s), state: "done",
        detail: r.retried ? "recovered on retry" : undefined,
      });
    } else {
      items.push({
        id: `step-${s.id}`, label: stepLabel(s), state: "fail",
        detail: r.message || r.status,
      });
    }
  }

  items.push(
    run.analysis
      ? {
          id: "debug", label: "debugger", state: "done",
          detail: run.analysis.failed ? "failure diagnosed" : "no remaining failure",
        }
      : { id: "debug", label: "debugger", state: "pending" },
  );

  const results = run.results ?? [];
  if (results.length > 0) {
    const failed = results.filter((r) => r.status !== "passed");
    items.push(
      failed.length === 0
        ? { id: "validation", label: "validation", state: "done" as CheckState }
        : {
            id: "validation", label: "validation", state: "fail" as CheckState,
            detail: `${failed.length} failed`,
          },
    );
  }
  if (hasEvent(events, "recovery")) {
    const recovered = results.filter((r) => r.retried && r.status === "passed");
    items.push(
      recovered.length > 0
        ? {
            id: "recovery", label: "recovery", state: "done" as CheckState,
            detail: recovered
              .map((r) => (r.recovered_from ? `'${r.recovered_from}' → retried` : "retried"))
              .join("; "),
          }
        : { id: "recovery", label: "recovery", state: "fail" as CheckState },
    );
  }

  items.push(
    run.report
      ? { id: "report", label: "report", state: "done" }
      : { id: "report", label: "report", state: "pending" },
  );

  const fix = run.fix_status ?? "none";
  if (fix !== "none") {
    items.push(
      fix === "verified"
        ? { id: "fix", label: "fix", state: "done" as CheckState, detail: "applied and verified" }
        : fix === "awaiting_approval"
          ? { id: "fix", label: "fix", state: "pending" as CheckState, detail: "awaiting approval" }
          : fix === "rejected"
            ? { id: "fix", label: "fix", state: "pending" as CheckState, detail: "declined — not applied" }
            : { id: "fix", label: "fix", state: "fail" as CheckState, detail: fix },
    );
  }

  return items;
}
