import { Link } from "react-router-dom";
import { Loader2, Play, RefreshCw } from "lucide-react";
import type { CriterionResult, CriterionStatus, GoNoGoReport, Recommendation } from "@/api/types";
import { CitationChip } from "@/components/CitationChip";
import { EmptyState } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCitationDrawer } from "@/hooks/useCitationDrawer";
import { cn, formatDate } from "@/lib/utils";

interface GoNoGoPanelProps {
  bidId: string;
  /** Saved assessment; null when none was run yet. */
  report: GoNoGoReport | null;
  onRun: () => void;
  running: boolean;
}

const RECOMMENDATION: Record<Recommendation, { label: string; className: string }> = {
  go: { label: "Go", className: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300" },
  no_go: { label: "No-go", className: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300" },
  review: { label: "Needs review", className: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300" },
};

const CRITERION: Record<CriterionStatus, { label: string; className: string }> = {
  met: { label: "met", className: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300" },
  not_met: { label: "not met", className: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300" },
  unknown: { label: "unknown", className: "bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-200" },
};

export const CAPABILITIES_HINT = "Judged against config/capabilities.yaml";

/** Big coloured pill with the recommendation ("Go" / "No-go" / "Needs review"). */
function RecommendationBadge({ recommendation }: { recommendation: Recommendation }) {
  const { label, className } = RECOMMENDATION[recommendation] ?? RECOMMENDATION.review;
  return (
    <span
      data-recommendation={recommendation}
      className={cn("inline-flex items-center rounded-full px-4 py-1.5 text-lg font-semibold", className)}
    >
      {label}
    </span>
  );
}

/** Small coloured pill for a criterion status. */
function CriterionBadge({ status }: { status: CriterionStatus }) {
  const { label, className } = CRITERION[status] ?? CRITERION.unknown;
  return (
    <span
      data-status={status}
      className={cn("inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", className)}
    >
      {label}
    </span>
  );
}

function RunButton({ onRun, running, rerun }: { onRun: () => void; running: boolean; rerun: boolean }) {
  return (
    <Button onClick={onRun} disabled={running} variant={rerun ? "outline" : "default"}>
      {running ? <Loader2 className="animate-spin" /> : rerun ? <RefreshCw /> : <Play />}
      {running ? "Assessing..." : rerun ? "Re-run assessment" : "Run assessment"}
    </Button>
  );
}

/** Go / no-go assessment: recommendation card + one row per capability criterion. */
export function GoNoGoPanel({ bidId, report, onRun, running }: GoNoGoPanelProps) {
  if (!report) {
    return (
      <EmptyState
        title="No assessment yet"
        message={CAPABILITIES_HINT}
        action={<RunButton onRun={onRun} running={running} rerun={false} />}
      />
    );
  }
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-col gap-3 space-y-0 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <RecommendationBadge recommendation={report.recommendation} />
            <CardTitle className="text-base font-normal text-muted-foreground">
              {report.met} met · {report.not_met} not met · {report.unknown} unknown
            </CardTitle>
          </div>
          <RunButton onRun={onRun} running={running} rerun />
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{report.summary}</p>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>{CAPABILITIES_HINT}</span>
            <span>Generated {formatDate(report.generated_at)}</span>
            <Link to={`/runs/${encodeURIComponent(report.run_id)}`} className="break-all font-mono underline-offset-4 hover:underline">
              run {report.run_id}
            </Link>
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="p-0">
          <CriteriaTable bidId={bidId} criteria={report.criteria} />
        </CardContent>
      </Card>
    </div>
  );
}

function CriteriaTable({ bidId, criteria }: { bidId: string; criteria: CriterionResult[] }) {
  const { open } = useCitationDrawer();
  if (criteria.length === 0) {
    return <p className="p-5 text-sm text-muted-foreground">No criteria were evaluated.</p>;
  }
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Criterion</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Reason</TableHead>
            <TableHead className="hidden md:table-cell">Fields used</TableHead>
            <TableHead className="hidden lg:table-cell">Citations</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {criteria.map((c) => (
            <TableRow key={c.criterion}>
              <TableCell className="min-w-[10rem] align-top font-medium">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span>{c.label}</span>
                  {c.critical && <Badge variant="destructive">critical</Badge>}
                </div>
              </TableCell>
              <TableCell className="align-top">
                <CriterionBadge status={c.status} />
              </TableCell>
              <TableCell className="min-w-[14rem] max-w-md align-top text-sm">
                <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">{c.reason || "-"}</span>
              </TableCell>
              <TableCell className="hidden align-top text-xs text-muted-foreground md:table-cell">
                {c.fields.length === 0 ? "-" : c.fields.join(", ")}
              </TableCell>
              <TableCell className="hidden align-top lg:table-cell">
                <div className="flex flex-wrap gap-1">
                  {c.sources.length === 0 && <span className="text-xs text-muted-foreground">-</span>}
                  {c.sources.map((s, i) => (
                    <CitationChip
                      key={`${s.chunk_id}-${i}`}
                      citation={s}
                      className="lg:max-w-[11rem] xl:max-w-[16rem]"
                      onClick={() => open({ title: c.label, bidId, citations: c.sources, notes: c.reason, activeIndex: i })}
                    />
                  ))}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
