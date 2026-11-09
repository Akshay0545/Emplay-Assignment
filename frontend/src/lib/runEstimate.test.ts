import { describe, expect, it } from "vitest";
import type { StepLog } from "@/api/types";
import { estimateRun, expectedNodes, formatSeconds, formatStepSeconds } from "./runEstimate";

function step(node: string, latencyMs: number, n = 1): StepLog {
  return {
    run_id: "r", step: n, agent: "a", node, started_at: "", input_summary: "", tool_calls: [],
    output_summary: "", tokens_in: 0, tokens_out: 0, cost_usd: 0, latency_ms: latencyMs, error: null,
  };
}

describe("estimateRun", () => {
  it("uses typical timings before any step has run", () => {
    const e = estimateRun([], "extract");
    expect(e.elapsedS).toBe(0);
    expect(e.remainingS).toBeGreaterThan(300);
    expect(e.totalS).toBe(e.remainingS);
  });

  it("replaces typical timings with measured averages of completed nodes", () => {
    const steps = [step("ingest", 1000, 1), step("orchestrate", 0, 2), step("retrieve", 120000, 3)];
    const e = estimateRun(steps, "extract");
    expect(e.elapsedS).toBe(121);
    // three more retrieves are expected at the measured 120 s each
    expect(e.remainingS).toBeGreaterThanOrEqual(3 * 120);
    expect(e.totalS).toBe(e.elapsedS + e.remainingS);
  });

  it("has nothing remaining once every expected node has run", () => {
    const steps = expectedNodes("extract").map((node, i) => step(node, 1000, i + 1));
    expect(estimateRun(steps, "extract").remainingS).toBe(0);
  });

  it("does not break on an unexpected or repeated node", () => {
    const steps = [step("ingest", 1000, 1), step("surprise", 5, 2), step("retrieve", 1000, 3)];
    const e = estimateRun(steps, "extract");
    expect(e.remainingS).toBeGreaterThan(0);
    expect(Number.isFinite(e.totalS)).toBe(true);
  });

  it("single-node runs expect one step", () => {
    expect(expectedNodes("compare")).toEqual(["compare"]);
    expect(estimateRun([step("go_no_go", 4000)], "go_no_go").remainingS).toBe(0);
  });
});

describe("formatting", () => {
  it("formats durations for people", () => {
    expect(formatSeconds(45.4)).toBe("45 s");
    expect(formatSeconds(200)).toBe("3 min 20 s");
    expect(formatSeconds(180)).toBe("3 min");
    expect(formatSeconds(3720)).toBe("1 h 2 min");
    expect(formatStepSeconds(74527)).toBe("74.5 s");
  });
});
