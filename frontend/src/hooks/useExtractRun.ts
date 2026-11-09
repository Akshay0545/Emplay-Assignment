import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import type { RunInfo, RunStatus } from "@/api/types";
import { bidsQueryKey } from "./useBids";
import { recordQueryKey } from "./useRecord";

export const POLL_INTERVAL_MS = 1500;
export const IDLE_POLL_INTERVAL_MS = 5000;
export const runsQueryKey = ["runs"] as const;

function isFinished(status: RunStatus | undefined): boolean {
  return status?.status === "done" || status?.status === "failed" || status?.status === "cancelled";
}

/** Poll one run every 1.5 s until it is done or failed. */
export function useRun(runId: string | undefined) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (query) => (isFinished(query.state.data) ? false : POLL_INTERVAL_MS),
  });
}

/**
 * Runs known to the API (GET /runs). Polled quickly while one is running so
 * every page sees the same "busy" state, slowly otherwise.
 */
export function useRuns() {
  return useQuery({
    queryKey: runsQueryKey,
    queryFn: api.listRuns,
    refetchInterval: (query) =>
      query.state.data?.some((r) => r.status === "running") ? POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS,
  });
}

/** The extraction holding the extraction lock, if any (questions never hold it). */
export function activeRun(runs: RunInfo[] | undefined): RunInfo | undefined {
  return runs?.find((r) => r.status === "running" && r.kind === "extract");
}

/**
 * Start an extraction for a bid and follow its progress. A run already in
 * progress on the server for this bid (started from another page or before a
 * reload) is adopted; a run for another bid is reported as `busyWith`. When
 * the run finishes, the record and bid list are refetched.
 */
export function useExtractRun(bidId: string) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<string | undefined>();
  const [settled, setSettled] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const runs = useRuns();
  const active = activeRun(runs.data);

  const start = useMutation({
    mutationFn: () => api.extract(bidId),
    onSuccess: (data) => {
      setSettled(false);
      setStopRequested(false);
      setRunId(data.run_id);
      void queryClient.invalidateQueries({ queryKey: runsQueryKey });
    },
    onError: (err) => {
      // A 409 carries the id of the run that holds the lock; follow it when it is ours.
      if (err instanceof ApiError && err.status === 409) {
        void queryClient.invalidateQueries({ queryKey: runsQueryKey });
        if (err.runId && active?.bid_id === bidId) {
          setSettled(false);
          setRunId(err.runId);
        }
      }
    },
  });

  // Adopt a server-side run for this bid that this page did not start.
  useEffect(() => {
    if (!runId && active && active.bid_id === bidId) {
      setSettled(false);
      setRunId(active.run_id);
    }
  }, [runId, active, bidId]);

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId as string),
    enabled: Boolean(runId),
    refetchInterval: (query) => (isFinished(query.state.data) ? false : POLL_INTERVAL_MS),
  });

  const finished = isFinished(run.data);
  useEffect(() => {
    if (!finished || settled) return;
    setSettled(true);
    void queryClient.invalidateQueries({ queryKey: recordQueryKey(bidId) });
    void queryClient.invalidateQueries({ queryKey: bidsQueryKey });
    void queryClient.invalidateQueries({ queryKey: runsQueryKey });
  }, [finished, settled, bidId, queryClient]);

  // Asks the server to stop; the run keeps reporting until the step in flight
  // notices the flag (checked before every LLM call and between retrieval
  // fields), then its status becomes "cancelled" and polling stops.
  const cancel = useMutation({
    mutationFn: () => api.cancelRun(runId as string),
    onSuccess: () => {
      setStopRequested(true);
      void queryClient.invalidateQueries({ queryKey: runsQueryKey });
    },
  });

  const reset = useCallback(() => {
    setRunId(undefined);
    setSettled(false);
    setStopRequested(false);
  }, []);

  const conflict = start.error instanceof ApiError && start.error.status === 409;
  const running = Boolean(runId) && !finished;
  const busyWith = !running && active && active.bid_id !== bidId ? active.bid_id : undefined;

  return {
    start: start.mutate,
    starting: start.isPending,
    startError: start.error,
    conflict,
    runId,
    status: run.data,
    running,
    /** Another bid's run holds the extraction lock. */
    busyWith,
    finished,
    /** Ask the running extraction to stop at its next step. */
    cancel: cancel.mutate,
    cancelling: cancel.isPending,
    /** A stop was requested and the run has not wound down yet. */
    stopping: stopRequested && Boolean(runId) && !finished,
    reset,
  };
}
