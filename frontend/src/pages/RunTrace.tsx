import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { ChatMessage } from "@/components/ChatMessage";
import { RunSteps } from "@/components/RunSteps";
import { ErrorState, ListSkeleton } from "@/components/States";
import { Card, CardContent } from "@/components/ui/card";
import type { RunKind, RunStatus } from "@/api/types";
import { useRun } from "@/hooks/useExtractRun";

const TITLES: Record<RunKind, string> = {
  extract: "Run trace",
  qa: "Question trace",
  compare: "Comparison trace",
  go_no_go: "Go / no-go trace",
};

/** Where the back link of a run points, by kind. */
function backLink(run: RunStatus): { to: string; label: string } {
  const bid = run.bid_id.split(",")[0];
  switch (run.kind) {
    case "qa":
      return { to: "/ask", label: "Ask" };
    case "compare":
      return { to: "/compare", label: "Compare" };
    case "go_no_go":
      return bid
        ? { to: `/bids/${encodeURIComponent(bid)}?tab=go-no-go`, label: `${bid} · Go / no-go` }
        : { to: "/", label: "Bids" };
    default:
      return bid ? { to: `/bids/${encodeURIComponent(bid)}`, label: bid } : { to: "/", label: "Bids" };
  }
}

/** Full step timeline of one run (/runs/:runId). */
export function RunTracePage() {
  const { runId = "" } = useParams<{ runId: string }>();
  const run = useRun(runId);
  const back = run.data ? backLink(run.data) : null;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        {back && (
          <Link to={back.to} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-3 w-3" /> {back.label}
          </Link>
        )}
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
          {run.data ? TITLES[run.data.kind] ?? "Run trace" : "Run trace"}
        </h1>
        <p className="break-all font-mono text-xs text-muted-foreground sm:text-sm">{runId}</p>
      </div>

      {run.isLoading && <ListSkeleton rows={6} />}
      {run.isError && (
        <ErrorState title="Could not load the run" message={run.error.message} onRetry={() => void run.refetch()} />
      )}
      {run.data?.kind === "qa" && run.data.answer && (
        <ChatMessage entry={{ kind: "answer", id: run.data.run_id, answer: run.data.answer }} />
      )}
      {run.data?.status === "failed" && run.data.error && (
        <ChatMessage entry={{ kind: "error", id: `${run.data.run_id}-error`, text: run.data.error }} />
      )}
      {run.data && (
        <Card>
          <CardContent className="p-4 sm:p-5">
            <RunSteps steps={run.data.steps} status={run.data.status} kind={run.data.kind} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
