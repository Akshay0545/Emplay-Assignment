import { useEffect, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { Columns3, Loader2 } from "lucide-react";
import { toast } from "sonner";
import type { ComparisonCell, ComparisonHighlight, ComparisonReport, ComparisonRow } from "@/api/types";
import { CitationChip } from "@/components/CitationChip";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/States";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useBids } from "@/hooks/useBids";
import { useCitationDrawer } from "@/hooks/useCitationDrawer";
import { useLatestComparison, useRunComparison } from "@/hooks/useCompare";
import { cn, formatDate, formatValue } from "@/lib/utils";

const NOT_FOUND_TEXT = "Not found in documents";

/** Compare page: pick two or more bids, run the comparison agent, browse the side-by-side result. */
export function ComparePage() {
  const bids = useBids();
  const latest = useLatestComparison();
  const run = useRunComparison();
  const [selected, setSelected] = useState<string[]>([]);
  const [initialised, setInitialised] = useState(false);

  // Pre-select the first two bids once the list arrives.
  useEffect(() => {
    if (initialised || !bids.data) return;
    setSelected(bids.data.slice(0, 2).map((b) => b.bid_id));
    setInitialised(true);
  }, [bids.data, initialised]);

  useEffect(() => {
    if (run.error) toast.error(run.error.message);
  }, [run.error]);

  const toggle = (bidId: string) =>
    setSelected((cur) => (cur.includes(bidId) ? cur.filter((id) => id !== bidId) : [...cur, bidId]));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Compare bids"
        description="Put the extracted fields of two or more bids side by side and see where they differ."
      />

      <Card>
        <CardHeader>
          <CardTitle>Bids to compare</CardTitle>
          <CardDescription>Pick at least two bids with an extraction record.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {bids.isLoading && <ListSkeleton rows={2} />}
          {bids.isError && (
            <ErrorState title="Could not load bids" message={bids.error.message} onRetry={() => void bids.refetch()} />
          )}
          {bids.data && bids.data.length === 0 && <EmptyState title="No bids yet" message="Add a bid first." />}
          {bids.data && bids.data.length > 0 && (
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {bids.data.map((b) => (
                <li key={b.bid_id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-md border p-3 text-sm hover:bg-muted">
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
                      checked={selected.includes(b.bid_id)}
                      onChange={() => toggle(b.bid_id)}
                      disabled={run.isPending}
                    />
                    <span className="min-w-0">
                      <span className="block font-mono">{b.bid_id}</span>
                      {b.title && <span className="block truncate text-xs text-muted-foreground" title={b.title}>{b.title}</span>}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <Button onClick={() => run.mutate(selected)} disabled={selected.length < 2 || run.isPending}>
            {run.isPending ? <Loader2 className="animate-spin" /> : <Columns3 />}
            {run.isPending ? "Comparing..." : `Compare ${selected.length} bid${selected.length === 1 ? "" : "s"}`}
          </Button>
        </CardContent>
      </Card>

      {latest.isLoading && <ListSkeleton rows={6} />}
      {latest.isError && (
        <ErrorState title="Could not load the latest comparison" message={latest.error.message} onRetry={() => void latest.refetch()} />
      )}
      {latest.data === null && !run.isPending && (
        <EmptyState title="No comparison yet" message="Select bids above and run a comparison." />
      )}
      {latest.data && <ComparisonResult report={latest.data} />}
    </div>
  );
}

/** Narrative, highlights and the side-by-side table of one comparison report. */
export function ComparisonResult({ report }: { report: ComparisonReport }) {
  const [onlyDifferences, setOnlyDifferences] = useState(false);
  const rows = onlyDifferences ? report.rows.filter((r) => r.differs) : report.rows;
  const differing = report.rows.filter((r) => r.differs).length;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Summary</CardTitle>
          <CardDescription className="flex flex-wrap gap-x-4 gap-y-1">
            <span>{report.bid_ids.join(" vs ")}</span>
            <span>Generated {formatDate(report.generated_at)}</span>
            <Link to={`/runs/${encodeURIComponent(report.run_id)}`} className="break-all font-mono text-xs underline-offset-4 hover:underline">
              run {report.run_id}
            </Link>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {report.narrative ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{report.narrative}</p>
          ) : (
            <p className="text-sm italic text-muted-foreground">No narrative.</p>
          )}
          <Highlights highlights={report.highlights} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-col gap-2 space-y-0 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>Fields side by side</CardTitle>
            <CardDescription>{differing} of {report.rows.length} fields differ. Click a cell to see its sources.</CardDescription>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4 accent-primary"
              checked={onlyDifferences}
              onChange={(e) => setOnlyDifferences(e.target.checked)}
            />
            Only differences
          </label>
        </CardHeader>
        <CardContent className="p-0">
          {rows.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">
              {onlyDifferences ? "The bids agree on every field." : "No fields to show."}
            </p>
          ) : (
            <ComparisonTable bidIds={report.bid_ids} rows={rows} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Highlights({ highlights }: { highlights: ComparisonHighlight[] }) {
  const { open } = useCitationDrawer();
  if (highlights.length === 0) return null;
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">Highlights</h3>
      <ul className="space-y-2">
        {highlights.map((h, i) => (
          <li key={`${h.field}-${i}`} className="rounded-md border-l-2 border-primary/60 bg-accent/30 p-3 text-sm">
            <span className="font-medium">{h.field}</span>
            <span className="text-muted-foreground"> · </span>
            <span className="[overflow-wrap:anywhere]">{h.note}</span>
            {h.sources.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {h.sources.map((s, j) => (
                  <CitationChip
                    key={`${s.chunk_id}-${j}`}
                    citation={s}
                    // Highlights span bids: the drawer resolves each chunk's bid itself.
                    onClick={() => open({ title: h.field, bidId: null, citations: h.sources, notes: h.note, activeIndex: j })}
                  />
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ComparisonTable({ bidIds, rows }: { bidIds: string[]; rows: ComparisonRow[] }) {
  return (
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="min-w-[10rem]">Field</TableHead>
            {bidIds.map((id) => (
              <TableHead key={id} className="min-w-[14rem] font-mono">{id}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow
              key={row.field}
              data-differs={row.differs}
              className={cn(row.differs && "border-l-2 border-l-primary bg-accent/30")}
            >
              <TableCell className="align-top font-medium">{row.field}</TableCell>
              {bidIds.map((id) => {
                const cell = row.cells.find((c) => c.bid_id === id);
                return cell ? (
                  <ValueCell key={id} field={row.field} cell={cell} />
                ) : (
                  <TableCell key={id} className="align-top text-xs text-muted-foreground">-</TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** One bid's value of a field; clicking opens the citation drawer with the cell's sources. */
function ValueCell({ field, cell }: { field: string; cell: ComparisonCell }) {
  const { open } = useCitationDrawer();
  const text = formatValue(cell.value);
  const show = () => open({ title: `${field} (${cell.bid_id})`, bidId: cell.bid_id, citations: cell.sources, notes: cell.notes });
  const onKeyDown = (e: KeyboardEvent<HTMLTableCellElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      show();
    }
  };
  return (
    <TableCell
      tabIndex={0}
      role="button"
      aria-label={`Show citations for ${field} in ${cell.bid_id}`}
      className="cursor-pointer align-top focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      onClick={show}
      onKeyDown={onKeyDown}
    >
      <div className="space-y-1">
        {cell.value === null ? (
          <span className="block italic text-muted-foreground [overflow-wrap:anywhere]">{cell.notes || NOT_FOUND_TEXT}</span>
        ) : (
          <span className="line-clamp-4 whitespace-pre-wrap [overflow-wrap:anywhere]" title={text}>{text}</span>
        )}
        <StatusBadge status={cell.status} className="text-[10px]" />
      </div>
    </TableCell>
  );
}
