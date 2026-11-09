import { useEffect } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Loader2, Play, Square, Trash2 } from "lucide-react";
import type { AddendumChange, BidRecord, DocumentSummary, FieldValue } from "@/api/types";
import { CitationChip } from "@/components/CitationChip";
import { FieldsTable } from "@/components/FieldsTable";
import { GoNoGoPanel } from "@/components/GoNoGoPanel";
import { PageHeader } from "@/components/PageHeader";
import { RunSteps } from "@/components/RunSteps";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBidDetail, useRemoveBid } from "@/hooks/useBids";
import { useCitationDrawer } from "@/hooks/useCitationDrawer";
import { useExtractRun } from "@/hooks/useExtractRun";
import { useGoNoGo, useRunGoNoGo } from "@/hooks/useGoNoGo";
import { useRecord } from "@/hooks/useRecord";
import { docTypeLabel, formatDate, formatValue } from "@/lib/utils";

/** Field names (config/fields.yaml) used by the header and key-facts grid. */
const F = {
  bidNumber: "Bid Number",
  title: "Title",
  dueDate: "Due Date",
  submissionType: "Bid Submission Type",
  term: "Term of Bid",
  preBid: "Pre Bid Meeting",
  contact: "contact_info",
  agency: "company_name",
  summary: "Bid Summary",
} as const;

const KEY_FACTS: { label: string; field: string }[] = [
  { label: "Bid Number", field: F.bidNumber },
  { label: "Due Date", field: F.dueDate },
  { label: "Submission type", field: F.submissionType },
  { label: "Term", field: F.term },
  { label: "Pre-bid", field: F.preBid },
  { label: "Contact", field: F.contact },
  { label: "Agency", field: F.agency },
];

function fieldText(record: BidRecord | null | undefined, name: string): string | null {
  const field = record?.fields[name];
  if (!field || field.value === null) return null;
  return formatValue(field.value);
}

/** Bid detail: header, Overview / Fields / Addendum changes / Documents / Go no-go tabs. */
export function BidDetailPage() {
  const { bidId = "" } = useParams<{ bidId: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const detail = useBidDetail(bidId);
  const removal = useRemoveBid();
  const record = useRecord(bidId);
  const run = useExtractRun(bidId);
  const goNoGo = useGoNoGo(bidId);
  const runGoNoGo = useRunGoNoGo(bidId);
  // An upload starts the extraction on the server, so no auto-start is needed
  // here: useExtractRun adopts a run already in progress for this bid.

  useEffect(() => {
    if (!run.startError) return;
    toast.error(run.conflict ? `Busy: ${run.startError.message}.` : run.startError.message);
  }, [run.startError, run.conflict]);

  useEffect(() => {
    if (!run.finished || !run.status) return;
    if (run.status.status === "done") toast.success(`Extraction finished for ${bidId}`);
    else if (run.status.status === "cancelled") toast.info(`Extraction stopped for ${bidId}`);
    else toast.error(`Extraction failed for ${bidId}`);
  }, [run.finished, run.status, bidId]);

  useEffect(() => {
    if (runGoNoGo.error) toast.error(runGoNoGo.error.message);
  }, [runGoNoGo.error]);

  const rec = record.data ?? null;
  const title = fieldText(rec, F.title);
  const tab = params.get("tab") ?? "overview";

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={
          <Link to="/" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-3 w-3" /> All bids
          </Link>
        }
        title={title ?? bidId}
        description={
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-mono">{bidId}</span>
            {fieldText(rec, F.bidNumber) && <span>· {fieldText(rec, F.bidNumber)}</span>}
            {fieldText(rec, F.agency) && <span className="min-w-0">· {fieldText(rec, F.agency)}</span>}
            {fieldText(rec, F.dueDate) && <span>· due {fieldText(rec, F.dueDate)}</span>}
            {detail.data && (
              <Badge variant={detail.data.indexed ? "accent" : "secondary"}>
                {detail.data.indexed ? `indexed · ${detail.data.chunks} chunks` : "not indexed"}
              </Badge>
            )}
          </div>
        }
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => {
                const warning = run.running
                  ? `Remove ${bidId}? This stops the running extraction and deletes its index entries, outputs and uploaded files.`
                  : `Remove ${bidId}? This deletes its index entries, outputs and uploaded files.`;
                if (!window.confirm(warning)) return;
                removal.mutate(bidId, {
                  onSuccess: () => {
                    toast.success(`Removed ${bidId}`);
                    navigate("/");
                  },
                  onError: (err) => toast.error(err.message),
                });
              }}
              disabled={removal.isPending}
              title={run.running ? "Stops the running extraction, then deletes the bid." : undefined}
            >
              {removal.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
              {removal.isPending ? "Removing..." : "Remove bid"}
            </Button>
            <Button
              onClick={() => run.start()}
              disabled={run.starting || run.running || Boolean(run.busyWith)}
              title={run.busyWith ? `An extraction is running for ${run.busyWith}; wait for it to finish.` : undefined}
            >
              {run.starting || run.running || run.busyWith ? <Loader2 className="animate-spin" /> : <Play />}
              {run.running ? "Extracting..." : run.busyWith ? `Busy: extracting ${run.busyWith}` : "Run extraction"}
            </Button>
          </>
        }
      />

      {run.status && (
        <Card>
          <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
            <CardTitle>Extraction run</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Button asChild variant="link" size="sm">
                <Link to={`/runs/${encodeURIComponent(run.status.run_id)}`}>
                  Full trace <ArrowRight />
                </Link>
              </Button>
              {run.running && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => run.cancel()}
                  disabled={run.cancelling || run.stopping}
                  title="Stops as soon as the call in flight finishes"
                >
                  {run.cancelling || run.stopping ? <Loader2 className="animate-spin" /> : <Square />}
                  {run.cancelling || run.stopping ? "Stopping..." : "Stop"}
                </Button>
              )}
              {run.finished && (
                <Button variant="ghost" size="sm" onClick={run.reset}>
                  Dismiss
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <RunSteps steps={run.status.steps} status={run.status.status} kind={run.status.kind} compact />
          </CardContent>
        </Card>
      )}

      <Tabs value={tab} onValueChange={(v) => { params.set("tab", v); setParams(params, { replace: true }); }}>
        <div className="-mx-4 scroll-x px-4 no-scrollbar sm:mx-0 sm:px-0">
          <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="fields">Fields</TabsTrigger>
          <TabsTrigger value="changes">
            Addendum changes
            {rec && rec.addendum_changes.length > 0 && (
              <Badge variant="secondary" className="ml-2">{rec.addendum_changes.length}</Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="documents">Documents</TabsTrigger>
          <TabsTrigger value="go-no-go">Go / no-go</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="overview">
          <RecordGate record={record} onRun={() => run.start()} running={run.running || Boolean(run.busyWith)}>
            {(r) => <Overview record={r} />}
          </RecordGate>
        </TabsContent>
        <TabsContent value="fields">
          <RecordGate record={record} onRun={() => run.start()} running={run.running || Boolean(run.busyWith)}>
            {(r) => (
              <Card>
                <CardContent className="p-0">
                  <FieldsTable bidId={bidId} fields={r.fields} />
                </CardContent>
              </Card>
            )}
          </RecordGate>
        </TabsContent>
        <TabsContent value="changes">
          <RecordGate record={record} onRun={() => run.start()} running={run.running || Boolean(run.busyWith)}>
            {(r) => <AddendumTimeline bidId={bidId} changes={r.addendum_changes} />}
          </RecordGate>
        </TabsContent>
        <TabsContent value="documents">
          {detail.isLoading && <ListSkeleton />}
          {detail.isError && (
            <ErrorState title="Could not load documents" message={detail.error.message} onRetry={() => void detail.refetch()} />
          )}
          {detail.data && <DocumentsTable documents={detail.data.documents} />}
        </TabsContent>
        <TabsContent value="go-no-go">
          <RecordGate record={record} onRun={() => run.start()} running={run.running || Boolean(run.busyWith)}>
            {() => (
              <GoNoGoGate
                bidId={bidId}
                assessment={goNoGo}
                onRun={() => runGoNoGo.mutate()}
                running={runGoNoGo.isPending}
              />
            )}
          </RecordGate>
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** Loading / error / empty handling shared by the record-based tabs. */
function RecordGate({
  record,
  onRun,
  running,
  children,
}: {
  record: ReturnType<typeof useRecord>;
  onRun: () => void;
  running: boolean;
  children: (record: BidRecord) => React.ReactNode;
}) {
  if (record.isLoading) return <ListSkeleton rows={8} />;
  if (record.isError) {
    return <ErrorState title="Could not load the record" message={record.error.message} onRetry={() => void record.refetch()} />;
  }
  if (!record.data) {
    return (
      <EmptyState
        title="No extraction yet"
        message="Run the multi-agent extraction to fill the 20 fields with cited values."
        action={
          <Button onClick={onRun} disabled={running}>
            {running ? <Loader2 className="animate-spin" /> : <Play />} Run extraction
          </Button>
        }
      />
    );
  }
  return <>{children(record.data)}</>;
}

/** Loading / error handling for the go / no-go tab; the panel shows its own empty state. */
function GoNoGoGate({
  bidId,
  assessment,
  onRun,
  running,
}: {
  bidId: string;
  assessment: ReturnType<typeof useGoNoGo>;
  onRun: () => void;
  running: boolean;
}) {
  if (assessment.isLoading) return <ListSkeleton rows={4} />;
  if (assessment.isError) {
    return <ErrorState title="Could not load the assessment" message={assessment.error.message} onRetry={() => void assessment.refetch()} />;
  }
  return <GoNoGoPanel bidId={bidId} report={assessment.data ?? null} onRun={onRun} running={running} />;
}

function Overview({ record }: { record: BidRecord }) {
  const summary = fieldText(record, F.summary);
  const { open } = useCitationDrawer();
  const openField = (name: string, field: FieldValue) =>
    open({ title: name, bidId: record.bid_id, citations: field.sources, notes: field.notes });

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Bid Summary</CardTitle>
        </CardHeader>
        <CardContent>
          {summary ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{summary}</p>
          ) : (
            <p className="text-sm italic text-muted-foreground">Not found in documents</p>
          )}
          {record.fields[F.summary]?.sources.length ? (
            <Button variant="link" size="sm" className="mt-2 px-0" onClick={() => openField(F.summary, record.fields[F.summary])}>
              View sources ({record.fields[F.summary].sources.length})
            </Button>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Key facts</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-1">
            {KEY_FACTS.map(({ label, field }) => {
              const value = fieldText(record, field);
              const fv = record.fields[field];
              return (
                <div key={field}>
                  <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
                  <dd className="mt-0.5 break-words">
                    {value ? (
                      fv?.sources.length ? (
                        <button
                          type="button"
                          className="text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          onClick={() => openField(field, fv)}
                        >
                          {value}
                        </button>
                      ) : (
                        value
                      )
                    ) : (
                      <span className="italic text-muted-foreground">Not found in documents</span>
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
        </CardContent>
      </Card>
      <Card className="lg:col-span-3">
        <CardContent className="flex flex-col gap-2 p-4 text-sm text-muted-foreground sm:flex-row sm:flex-wrap sm:items-center sm:gap-4">
          <span>
            Validation: <strong className="text-green-700 dark:text-green-400">{record.validation.passed} passed</strong>,{" "}
            <strong className="text-red-700 dark:text-red-400">{record.validation.failed} failed</strong>,{" "}
            <strong>{record.validation.not_found} not found</strong>
          </span>
          <span>Generated {formatDate(record.generated_at)}</span>
          <Link to={`/runs/${encodeURIComponent(record.run_id)}`} className="break-all font-mono text-xs underline-offset-4 hover:underline">
            run {record.run_id}
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}

function AddendumTimeline({ bidId, changes }: { bidId: string; changes: AddendumChange[] }) {
  const { open } = useCitationDrawer();
  if (changes.length === 0) {
    return <EmptyState title="No addendum changes" message="No addendum overrode, clarified or added a field value." />;
  }
  const sorted = [...changes].sort((a, b) => a.addendum_number - b.addendum_number);
  return (
    <ol className="relative ml-2 space-y-4 border-l pl-6 sm:ml-0">
      {sorted.map((c, i) => (
        <li key={`${c.field}-${c.addendum_number}-${i}`} className="relative">
          <span className="absolute -left-[31px] top-1 flex h-5 w-5 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-primary-foreground">
            {c.addendum_number}
          </span>
          <Card>
            <CardContent className="space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{c.field}</span>
                <Badge variant="secondary">{c.kind}</Badge>
                <span className="text-xs text-muted-foreground">Addendum {c.addendum_number}</span>
              </div>
              <div className="grid grid-cols-1 items-center gap-2 text-sm sm:grid-cols-[1fr_auto_1fr] sm:gap-3">
                <div className="min-w-0 break-words rounded-md bg-muted p-2 line-through decoration-muted-foreground/60">
                  {c.old_value ?? <span className="italic text-muted-foreground">(none)</span>}
                </div>
                <ArrowRight className="h-4 w-4 rotate-90 justify-self-center text-muted-foreground sm:rotate-0" aria-hidden="true" />
                <div className="min-w-0 break-words rounded-md border border-primary/40 bg-accent/40 p-2">{c.new_value}</div>
              </div>
              <CitationChip
                citation={c.source}
                onClick={() => open({ title: `${c.field} (Addendum ${c.addendum_number})`, bidId, citations: [c.source] })}
              />
            </CardContent>
          </Card>
        </li>
      ))}
    </ol>
  );
}

function DocumentsTable({ documents }: { documents: DocumentSummary[] }) {
  if (documents.length === 0) {
    return <EmptyState title="No documents" message="This bid folder has no parsed files." />;
  }
  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead>Type</TableHead>
              <TableHead className="hidden sm:table-cell">Addendum</TableHead>
              <TableHead className="text-right">Pages</TableHead>
              <TableHead className="hidden text-right sm:table-cell">Tables</TableHead>
              <TableHead className="hidden md:table-cell">Warnings</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {documents.map((d) => (
              <TableRow key={d.file_name}>
                <TableCell className="max-w-[12rem] font-mono text-xs [overflow-wrap:anywhere] sm:max-w-none">{d.file_name}</TableCell>
                <TableCell><Badge variant="secondary" className="whitespace-nowrap">{docTypeLabel(d.doc_type)}</Badge></TableCell>
                <TableCell className="hidden sm:table-cell">{d.addendum_number ?? "-"}</TableCell>
                <TableCell className="text-right tabular-nums">{d.pages}</TableCell>
                <TableCell className="hidden text-right tabular-nums sm:table-cell">{d.tables}</TableCell>
                <TableCell className="hidden md:table-cell">
                  {d.warnings.length === 0 ? (
                    <span className="text-xs text-muted-foreground">none</span>
                  ) : (
                    <ul className="space-y-0.5 text-xs text-amber-700 dark:text-amber-400">
                      {d.warnings.map((w, i) => <li key={i}>{w}</li>)}
                    </ul>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
