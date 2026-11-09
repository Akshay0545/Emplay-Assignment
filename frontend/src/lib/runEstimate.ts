import type { RunKind, StepLog } from "@/api/types";

/**
 * Expected duration of a run, from the steps recorded so far.
 *
 * The graphs have a fixed shape (see rfp_intel/agents/graph.py): an extraction is
 * ingest, orchestrate, one retrieve + extract per field group, reconcile, validate,
 * then at most two validator retry rounds (retrieve, the retried groups, validate)
 * and the report. The estimate assumes one retry round, which is the usual case,
 * and uses the average duration of the completed steps of each node (falling back
 * to typical CPU timings) for the steps still ahead.
 */

/** Typical seconds per node on CPU; replaced by measured averages as steps complete. */
const TYPICAL_S: Record<string, number> = {
  ingest: 2,
  orchestrate: 0,
  retrieve: 90,
  extract_product_specs: 25,
  extract_dates_logistics: 15,
  extract_commercial_legal: 20,
  reconcile: 1,
  validate: 8,
  report: 15,
  answer: 15,
  validate_answer: 5,
  compare: 10,
  go_no_go: 5,
};

/** The nodes a run is expected to execute, in order (one typical retry round for extractions). */
export function expectedNodes(kind: RunKind): string[] {
  switch (kind) {
    case "extract": {
      const pass = ["retrieve", "extract_product_specs", "retrieve", "extract_dates_logistics", "retrieve", "extract_commercial_legal"];
      const retry = ["retrieve", "extract_dates_logistics", "extract_commercial_legal", "validate"];
      return ["ingest", "orchestrate", ...pass, "reconcile", "validate", ...retry, "report"];
    }
    case "qa":
      return ["orchestrate", "retrieve", "answer", "validate_answer"];
    case "compare":
      return ["compare"];
    case "go_no_go":
      return ["go_no_go"];
  }
}

export interface RunEstimate {
  /** Seconds spent in the completed steps. */
  elapsedS: number;
  /** Expected seconds still to come (0 once the run is past the expected shape). */
  remainingS: number;
  /** Expected total seconds (elapsed + remaining). */
  totalS: number;
}

/** Estimate elapsed, remaining and total seconds for a run from its completed steps. */
export function estimateRun(steps: StepLog[], kind: RunKind): RunEstimate {
  const elapsedS = steps.reduce((sum, s) => sum + s.latency_ms, 0) / 1000;
  const measured = averageByNode(steps);
  const remaining = remainingNodes(steps, kind);
  const remainingS = remaining.reduce((sum, node) => sum + (measured[node] ?? TYPICAL_S[node] ?? 10), 0);
  return { elapsedS, remainingS, totalS: elapsedS + remainingS };
}

/** Average measured seconds per node over the completed steps. */
function averageByNode(steps: StepLog[]): Record<string, number> {
  const sums: Record<string, { total: number; count: number }> = {};
  for (const step of steps) {
    const entry = (sums[step.node] ??= { total: 0, count: 0 });
    entry.total += step.latency_ms / 1000;
    entry.count += 1;
  }
  return Object.fromEntries(Object.entries(sums).map(([node, { total, count }]) => [node, total / count]));
}

/**
 * Expected nodes not yet executed: the expected sequence with the completed steps
 * consumed in order (a step that does not match the next expected node is skipped
 * over, so an extra retry round or a different group order never breaks the count).
 */
function remainingNodes(steps: StepLog[], kind: RunKind): string[] {
  const expected = expectedNodes(kind);
  let cursor = 0;
  for (const step of steps) {
    const next = expected.indexOf(step.node, cursor);
    if (next >= 0) cursor = next + 1;
  }
  return expected.slice(cursor);
}

/** "45 s", "3 min 20 s" or "1 h 2 min" for a duration in seconds. */
export function formatSeconds(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole} s`;
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) {
    const rest = whole % 60;
    return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const restMin = minutes % 60;
  return restMin ? `${hours} h ${restMin} min` : `${hours} h`;
}

/** Seconds with one decimal for a step, e.g. "74.5 s". */
export function formatStepSeconds(latencyMs: number): string {
  return `${(latencyMs / 1000).toFixed(1)} s`;
}
