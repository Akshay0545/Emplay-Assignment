import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { Answer, AskRequest, RunStatus } from "@/api/types";
import { POLL_INTERVAL_MS } from "./useExtractRun";

export const BUSY_MESSAGE = "The server is busy answering other questions. Try again in a moment.";

export interface AskCallbacks {
  onAnswer: (answer: Answer) => void;
  onError: (message: string) => void;
}

/** What the server is doing now, inferred from the steps it has finished so far. */
export function progressLabel(status: RunStatus | undefined): string {
  const last = status?.steps.at(-1);
  if (!last) return "Planning the search";
  switch (last.node) {
    case "orchestrate":
      return "Searching the documents";
    case "retrieve":
      return "Drafting the answer";
    case "answer":
      return "Checking the answer";
    case "validate_answer":
      return "Redrafting the answer";
    default:
      return "Working";
  }
}

function isFinished(status: RunStatus | undefined): boolean {
  return status?.status === "done" || status?.status === "failed";
}

/**
 * Ask a question in the background: POST /ask/runs, then poll GET /runs/{id}
 * until it is done or failed. While pending, `label` and `elapsedS` describe
 * the progress and `runId` links to the trace.
 */
export function useAsk() {
  const [runId, setRunId] = useState<string | undefined>();
  const [startedAt, setStartedAt] = useState<number | undefined>();
  const [elapsedS, setElapsedS] = useState(0);
  const callbacks = useRef<AskCallbacks | undefined>(undefined);

  const finish = useCallback(() => {
    setRunId(undefined);
    setStartedAt(undefined);
  }, []);

  const start = useMutation({
    mutationFn: (body: AskRequest) => api.startAsk(body),
    onSuccess: (data) => setRunId(data.run_id),
    onError: (err) => {
      finish();
      const busy = err instanceof ApiError && err.status === 429;
      callbacks.current?.onError(busy ? BUSY_MESSAGE : err.message);
    },
  });

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (query) => (isFinished(query.state.data) ? false : POLL_INTERVAL_MS),
  });

  // Deliver the result once the run has settled.
  useEffect(() => {
    const data = run.data;
    if (!runId || !data || data.status === "running") return;
    finish();
    if (data.status === "done" && data.answer) callbacks.current?.onAnswer(data.answer);
    else callbacks.current?.onError(data.error ?? "The question could not be answered.");
  }, [run.data, runId, finish]);

  // A failed poll ends the wait with the API's message.
  useEffect(() => {
    if (!runId || !run.isError) return;
    finish();
    callbacks.current?.onError(run.error.message);
  }, [run.isError, run.error, runId, finish]);

  useEffect(() => {
    if (!startedAt) {
      setElapsedS(0);
      return;
    }
    const tick = () => setElapsedS(Math.round((Date.now() - startedAt) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  const mutate = start.mutate;
  const submit = useCallback(
    (body: AskRequest, cb: AskCallbacks) => {
      callbacks.current = cb;
      setStartedAt(Date.now());
      mutate(body);
    },
    [mutate],
  );

  return {
    submit,
    pending: start.isPending || Boolean(runId),
    runId,
    status: run.data,
    label: progressLabel(run.data),
    elapsedS,
  };
}
