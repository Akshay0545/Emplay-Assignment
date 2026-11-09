import { AlertCircle, CheckCircle2, Loader2, Square } from "lucide-react";
import type { RunKind, RunState, StepLog } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { estimateRun, formatSeconds, formatStepSeconds } from "@/lib/runEstimate";
import { cn } from "@/lib/utils";

interface RunStepsProps {
  steps: StepLog[];
  status: RunState;
  kind?: RunKind;
  compact?: boolean;
}

/** Vertical timeline of StepLog entries with per-step timing and, while running, the expected duration. */
export function RunSteps({ steps, status, kind = "extract", compact = false }: RunStepsProps) {
  const errors = steps.filter((s) => s.error).length;
  const estimate = estimateRun(steps, kind);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <StatusPill status={status} />
        <span className="min-w-0 text-muted-foreground">
          {steps.length} step{steps.length === 1 ? "" : "s"} · {formatSeconds(estimate.elapsedS)}
          {status === "running" && (
            <span>
              {" "}· about {formatSeconds(estimate.remainingS)} remaining (expected total ~{formatSeconds(estimate.totalS)})
            </span>
          )}
          {errors > 0 && <span className="text-red-600 dark:text-red-400"> · {errors} error(s)</span>}
        </span>
      </div>
      {status === "running" && (
        <p className="text-xs text-muted-foreground">
          The estimate assumes the usual run shape (one retrieval and one extraction per field group, one validator retry
          round) and updates as steps complete; retrieval over large documents is the slowest part.
        </p>
      )}
      {steps.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {status === "running" ? "Waiting for the first step..." : "No steps were recorded."}
        </p>
      ) : (
        <ol className="relative ml-1 space-y-2 border-l pl-4">
          {steps.map((step) => (
            <li key={`${step.run_id}-${step.step}`} className="relative">
              <span
                className={cn(
                  "absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-background",
                  step.error ? "bg-red-500" : "bg-primary",
                )}
              />
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-mono text-xs text-muted-foreground">#{step.step}</span>
                <span className="font-medium">{step.agent}</span>
                <Badge variant="secondary" className="font-mono text-[11px]">
                  {step.node}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {formatStepSeconds(step.latency_ms)}
                </span>
              </div>
              {!compact && (
                <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                  {step.input_summary && <p className="truncate" title={step.input_summary}>in: {step.input_summary}</p>}
                  {step.output_summary && <p className="truncate" title={step.output_summary}>out: {step.output_summary}</p>}
                </div>
              )}
              {step.error && (
                <p className="mt-1 flex items-start gap-1 text-xs text-red-600 dark:text-red-400">
                  <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                  <span className="break-words">{step.error}</span>
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: RunState }) {
  if (status === "running") {
    return (
      <Badge variant="accent" className="gap-1">
        <Loader2 className="h-3 w-3 animate-spin" /> running
      </Badge>
    );
  }
  if (status === "failed") {
    return (
      <Badge variant="destructive" className="gap-1">
        <AlertCircle className="h-3 w-3" /> failed
      </Badge>
    );
  }
  if (status === "cancelled") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Square className="h-3 w-3" /> stopped
      </Badge>
    );
  }
  return (
    <Badge className="gap-1 bg-green-600 hover:bg-green-600">
      <CheckCircle2 className="h-3 w-3" /> done
    </Badge>
  );
}
