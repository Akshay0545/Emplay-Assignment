import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ExtractionEval, ExtractionFieldResult, RetrievalEval, RetrievalMetric } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useExtractionEval, useRetrievalEval } from "@/hooks/useEval";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

const METRICS: RetrievalMetric[] = ["recall@1", "recall@3", "recall@5", "recall@10", "mrr"];

/** Fixed categorical order (validated palette, light / dark steps). */
const SERIES_ORDER = ["vector", "hybrid", "hybrid_rerank"];
const SERIES_COLORS: Record<"light" | "dark", string[]> = {
  light: ["#2a78d6", "#eb6834", "#1baf7a"],
  dark: ["#3987e5", "#d95926", "#199e70"],
};

function orderedConfigs(summary: RetrievalEval["summary"]): string[] {
  const known = SERIES_ORDER.filter((c) => c in summary);
  const extra = Object.keys(summary).filter((c) => !SERIES_ORDER.includes(c)).sort();
  return [...known, ...extra].slice(0, SERIES_COLORS.light.length);
}

const EVAL_HINT = "Run `python main.py eval retrieval` and `python main.py eval extraction`, then reload.";

/** Evaluation page: retrieval metrics per config + extraction accuracy per field. */
export function EvaluationPage() {
  const retrieval = useRetrievalEval();
  const extraction = useExtractionEval();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Evaluation"
        description="Retrieval quality per configuration and extraction accuracy against the gold answers."
      />

      <section className="space-y-4" aria-labelledby="retrieval-heading">
        <h2 id="retrieval-heading" className="text-lg font-semibold">Retrieval</h2>
        {retrieval.isLoading && <ListSkeleton rows={4} />}
        {retrieval.isError && (
          <ErrorState title="Could not load retrieval results" message={retrieval.error.message} onRetry={() => void retrieval.refetch()} />
        )}
        {retrieval.data === null && <EmptyState title="No retrieval results yet" message={EVAL_HINT} />}
        {retrieval.data && <RetrievalSection data={retrieval.data} />}
      </section>

      <section className="space-y-4" aria-labelledby="extraction-heading">
        <h2 id="extraction-heading" className="text-lg font-semibold">Extraction</h2>
        {extraction.isLoading && <ListSkeleton rows={4} />}
        {extraction.isError && (
          <ErrorState title="Could not load extraction results" message={extraction.error.message} onRetry={() => void extraction.refetch()} />
        )}
        {extraction.data === null && <EmptyState title="No extraction results yet" message={EVAL_HINT} />}
        {extraction.data && <ExtractionSection data={extraction.data} />}
      </section>
    </div>
  );
}

function RetrievalSection({ data }: { data: RetrievalEval }) {
  const { theme } = useTheme();
  const colors = SERIES_COLORS[theme];
  const { configs, rows } = useMemo(() => {
    const summary = data.summary ?? {};
    const configs = orderedConfigs(summary);
    const rows = METRICS.map((metric) => {
      const row: Record<string, string | number> = { metric };
      for (const c of configs) row[c] = Number(summary[c]?.[metric] ?? 0);
      return row;
    });
    return { configs, rows };
  }, [data.summary]);
  const questions = data.questions ?? [];

  if (configs.length === 0) {
    return <EmptyState title="No retrieval summary" message="The results file has no per-config summary." />;
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle>Recall@k and MRR per configuration</CardTitle>
          <CardDescription>{questions.length} question{questions.length === 1 ? "" : "s"}; higher is better.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-56 sm:h-72" role="img" aria-label="Bar chart of recall at 1, 3, 5, 10 and MRR for each retrieval configuration">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} barGap={2} barCategoryGap="25%">
                <CartesianGrid vertical={false} stroke="hsl(var(--border))" />
                <XAxis dataKey="metric" tickLine={false} axisLine={false} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }} />
                <YAxis domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tickLine={false} axisLine={false} width={32} tick={{ fill: "hsl(var(--muted-foreground))", fontSize: 12 }} />
                <Tooltip
                  cursor={{ fill: "hsl(var(--muted))" }}
                  contentStyle={{ background: "hsl(var(--popover))", border: "1px solid hsl(var(--border))", borderRadius: 6, color: "hsl(var(--foreground))", fontSize: 12 }}
                  formatter={(value) => (typeof value === "number" ? value.toFixed(3) : String(value))}
                />
                <Legend wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
                {configs.map((c, i) => (
                  <Bar key={c} dataKey={c} name={c} fill={colors[i]} radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle>Summary table</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Metric</TableHead>
                {configs.map((c) => <TableHead key={c} className="text-right">{c}</TableHead>)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={String(r.metric)}>
                  <TableCell className="font-medium">{r.metric}</TableCell>
                  {configs.map((c) => (
                    <TableCell key={c} className="text-right tabular-nums">{Number(r[c]).toFixed(3)}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card className="xl:col-span-5">
        <CardHeader>
          <CardTitle>Per-question rank</CardTitle>
          <CardDescription>Rank of the expected file in each configuration's results (dash = not in top k).</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {questions.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">No per-question results.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Id</TableHead>
                  <TableHead>Question</TableHead>
                  <TableHead className="hidden md:table-cell">Bid</TableHead>
                  <TableHead className="hidden lg:table-cell">Expected file</TableHead>
                  {configs.map((c) => <TableHead key={c} className="text-right">{c}</TableHead>)}
                </TableRow>
              </TableHeader>
              <TableBody>
                {questions.map((q) => (
                  <TableRow key={q.id}>
                    <TableCell className="font-mono text-xs">{q.id}</TableCell>
                    <TableCell className="min-w-[14rem] max-w-md">
                      <span className="line-clamp-2" title={q.question}>{q.question}</span>
                      {q.key_phrase && <span className="block truncate text-xs text-muted-foreground" title={q.key_phrase}>"{q.key_phrase}"</span>}
                    </TableCell>
                    <TableCell className="hidden font-mono text-xs md:table-cell">{q.bid_id}</TableCell>
                    <TableCell className="hidden max-w-xs truncate font-mono text-xs lg:table-cell" title={q.expected_file}>{q.expected_file}</TableCell>
                    {configs.map((c) => <RankCell key={c} rank={q.ranks?.[c] ?? null} />)}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function RankCell({ rank }: { rank: number | null }) {
  const tone =
    rank === null ? "text-muted-foreground" : rank === 1 ? "text-green-700 dark:text-green-400" : rank <= 3 ? "text-amber-700 dark:text-amber-400" : "";
  return <TableCell className={cn("text-right tabular-nums", tone)}>{rank ?? "-"}</TableCell>;
}

const SCORE_CLASS: Record<string, string> = {
  exact: "border-green-500 text-green-700 dark:text-green-400",
  partial: "border-amber-500 text-amber-700 dark:text-amber-400",
  wrong: "border-red-500 text-red-700 dark:text-red-400",
  not_scored: "text-muted-foreground",
};

function show(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(show).join("; ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ExtractionSection({ data }: { data: ExtractionEval }) {
  const bids = Array.isArray(data.bids) ? data.bids : [];
  if (bids.length === 0) {
    return <EmptyState title="No extraction results yet" message={EVAL_HINT} />;
  }
  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        Exact = every gold token present; partial = {data.judge ? "LLM judge: same meaning" : "value present (judge off)"};
        accuracy = (exact + partial) / scored fields.
      </p>
      {bids.map((result) => {
        const fields: ExtractionFieldResult[] = Array.isArray(result.fields) ? result.fields : [];
        return (
          <Card key={result.bid_id}>
            <CardHeader className="flex-col gap-2 space-y-0 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
              <CardTitle className="font-mono">{result.bid_id}</CardTitle>
              <div className="flex flex-wrap gap-1.5 text-xs">
                <Badge>accuracy {(result.accuracy * 100).toFixed(0)}%</Badge>
                <Badge variant="outline" className={SCORE_CLASS.exact}>{result.exact} exact</Badge>
                <Badge variant="outline" className={SCORE_CLASS.partial}>{result.partial} partial</Badge>
                <Badge variant="outline" className={SCORE_CLASS.wrong}>{result.wrong} wrong</Badge>
                <Badge variant="outline" className="text-muted-foreground">{result.scored_fields} scored</Badge>
                {typeof result.values_without_citation === "number" && (
                  <Badge variant="outline" className={result.values_without_citation > 0 ? SCORE_CLASS.wrong : "text-muted-foreground"}>
                    {result.values_without_citation} without citation
                  </Badge>
                )}
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {fields.length === 0 ? (
                <p className="p-5 text-sm text-muted-foreground">No per-field results.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Field</TableHead>
                      <TableHead>Gold</TableHead>
                      <TableHead>Output</TableHead>
                      <TableHead>Score</TableHead>
                      <TableHead className="hidden md:table-cell">Reason</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {fields.map((f, i) => (
                      <TableRow key={`${f.field}-${i}`} className={f.counted === false ? "text-muted-foreground" : undefined}>
                        <TableCell className="font-medium">{f.field}</TableCell>
                        <TableCell className="min-w-[8rem] max-w-xs"><span className="line-clamp-2" title={show(f.gold)}>{show(f.gold)}</span></TableCell>
                        <TableCell className="min-w-[8rem] max-w-xs"><span className="line-clamp-2" title={show(f.output)}>{show(f.output)}</span></TableCell>
                        <TableCell>
                          <Badge variant="outline" className={SCORE_CLASS[f.score] ?? ""}>{String(f.score).replace("_", " ")}</Badge>
                        </TableCell>
                        <TableCell className="hidden max-w-sm text-xs text-muted-foreground md:table-cell">
                          <span className="line-clamp-2" title={f.reason ?? ""}>{f.reason ?? "-"}</span>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
