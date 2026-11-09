import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RunStatus } from "@/api/types";

vi.mock("@/api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/api/client")>();
  return { ...mod, api: { ...mod.api, startAsk: vi.fn(), getRun: vi.fn() } };
});

import { api, ApiError } from "@/api/client";
import { BUSY_MESSAGE, progressLabel, useAsk } from "@/hooks/useAsk";

const step = (node: string) =>
  ({ run_id: "r1", step: 1, agent: "x", node, started_at: "t", input_summary: "", tool_calls: [], output_summary: "",
     tokens_in: 0, tokens_out: 0, cost_usd: 0, latency_ms: 1, error: null }) as RunStatus["steps"][number];

const running: RunStatus = { run_id: "r1", bid_id: "Bid1", status: "running", steps: [step("orchestrate")], kind: "qa", answer: null, error: null, summary: null };
const done: RunStatus = {
  ...running,
  status: "done",
  steps: [step("orchestrate"), step("retrieve"), step("answer"), step("validate_answer")],
  answer: { question: "q", answer: "July 9, 2024 [1]", citations: [], confidence: 0.9, bids: ["Bid1"], cached: false },
};

let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;

describe("useAsk", () => {
  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.startAsk).mockReset();
    vi.mocked(api.getRun).mockReset();
  });

  it("starts a run, shows progress while polling and delivers the answer", async () => {
    vi.mocked(api.startAsk).mockResolvedValue({ run_id: "r1" });
    vi.mocked(api.getRun).mockResolvedValueOnce(running).mockResolvedValue(done);
    const onAnswer = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useAsk(), { wrapper });

    act(() => result.current.submit({ question: "When is it due?", bid_ids: ["Bid1"] }, { onAnswer, onError }));
    await waitFor(() => expect(result.current.runId).toBe("r1"));
    await waitFor(() => expect(result.current.label).toBe("Searching the documents"));
    expect(result.current.pending).toBe(true);

    await waitFor(() => expect(onAnswer).toHaveBeenCalledWith(done.answer), { timeout: 4000 });
    expect(onError).not.toHaveBeenCalled();
    expect(result.current.pending).toBe(false);
    expect(result.current.runId).toBeUndefined();
    expect(api.startAsk).toHaveBeenCalledWith({ question: "When is it due?", bid_ids: ["Bid1"] });
  });

  it("reports a full queue as a busy message", async () => {
    vi.mocked(api.startAsk).mockRejectedValue(new ApiError("the server is busy answering 10 questions", 429));
    const onAnswer = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useAsk(), { wrapper });

    act(() => result.current.submit({ question: "q" }, { onAnswer, onError }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith(BUSY_MESSAGE));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(result.current.pending).toBe(false);
    expect(api.getRun).not.toHaveBeenCalled();
  });

  it("reports a failed run with the server's reason", async () => {
    vi.mocked(api.startAsk).mockResolvedValue({ run_id: "r1" });
    vi.mocked(api.getRun).mockResolvedValue({ ...running, status: "failed", error: "timed out after 120 s" });
    const onError = vi.fn();
    const { result } = renderHook(() => useAsk(), { wrapper });

    act(() => result.current.submit({ question: "q" }, { onAnswer: vi.fn(), onError }));
    await waitFor(() => expect(onError).toHaveBeenCalledWith("timed out after 120 s"));
    expect(result.current.pending).toBe(false);
  });
});

describe("progressLabel", () => {
  it("names the step the server is on next", () => {
    expect(progressLabel(undefined)).toBe("Planning the search");
    expect(progressLabel(running)).toBe("Searching the documents");
    expect(progressLabel({ ...running, steps: [step("orchestrate"), step("retrieve")] })).toBe("Drafting the answer");
    expect(progressLabel({ ...running, steps: [step("answer")] })).toBe("Checking the answer");
    expect(progressLabel({ ...running, steps: [step("validate_answer")] })).toBe("Redrafting the answer");
  });
});
