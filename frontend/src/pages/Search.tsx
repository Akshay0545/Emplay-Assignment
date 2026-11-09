import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { Search as SearchIcon } from "lucide-react";
import { DOC_TYPES, SEARCH_CONFIGS, type DocType, type SearchConfig, type SearchParams } from "@/api/types";
import { PageHeader } from "@/components/PageHeader";
import { SearchResultCard } from "@/components/SearchResultCard";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useBids } from "@/hooks/useBids";
import { useSearch } from "@/hooks/useSearch";
import { docTypeLabel } from "@/lib/utils";

const ANY = "__any__";

function parseParams(sp: URLSearchParams): SearchParams {
  const addendum = sp.get("addendum");
  const docType = sp.get("doc_type");
  const config = sp.get("config");
  return {
    q: sp.get("q") ?? "",
    bid_id: sp.get("bid_id") ?? undefined,
    doc_type: docType && (DOC_TYPES as string[]).includes(docType) ? (docType as DocType) : undefined,
    addendum: addendum ? Number(addendum) : undefined,
    config: config && (SEARCH_CONFIGS as string[]).includes(config) ? (config as SearchConfig) : "hybrid_rerank",
    top_k: 10,
  };
}

/** Search page: query + filters, result cards opening the citation drawer. */
export function SearchPage() {
  const [sp, setSp] = useSearchParams();
  const submitted = parseParams(sp);
  const [draft, setDraft] = useState<SearchParams>(submitted);
  const bids = useBids();
  const results = useSearch(submitted, true);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams();
    if (draft.q.trim()) next.set("q", draft.q.trim());
    if (draft.bid_id) next.set("bid_id", draft.bid_id);
    if (draft.doc_type) next.set("doc_type", draft.doc_type);
    if (draft.addendum !== undefined && !Number.isNaN(draft.addendum)) next.set("addendum", String(draft.addendum));
    if (draft.config) next.set("config", draft.config);
    setSp(next);
  };

  const hits = results.data?.hits ?? [];

  return (
    <div className="space-y-6">
      <PageHeader title="Search" description="Hybrid vector + keyword search over every indexed document." />

      <form onSubmit={onSubmit} className="space-y-3 rounded-lg border bg-card p-3 shadow-sm sm:p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="Search query"
            placeholder="e.g. bid bond amount, warranty period, submission deadline"
            value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })}
            autoFocus
          />
          <Button type="submit" disabled={!draft.q.trim()} className="w-full sm:w-auto">
            <SearchIcon /> Search
          </Button>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Select value={draft.bid_id ?? ANY} onValueChange={(v) => setDraft({ ...draft, bid_id: v === ANY ? undefined : v })}>
            <SelectTrigger aria-label="Bid filter"><SelectValue placeholder="All bids" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>All bids</SelectItem>
              {(bids.data ?? []).map((b) => (
                <SelectItem key={b.bid_id} value={b.bid_id}>{b.bid_id}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={draft.doc_type ?? ANY} onValueChange={(v) => setDraft({ ...draft, doc_type: v === ANY ? undefined : (v as DocType) })}>
            <SelectTrigger aria-label="Document type filter"><SelectValue placeholder="All types" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>All document types</SelectItem>
              {DOC_TYPES.map((t) => (
                <SelectItem key={t} value={t}>{docTypeLabel(t)}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            type="number"
            min={1}
            aria-label="Addendum number filter"
            placeholder="Addendum #"
            value={draft.addendum ?? ""}
            onChange={(e) => setDraft({ ...draft, addendum: e.target.value === "" ? undefined : Number(e.target.value) })}
          />
          <Select value={draft.config ?? "hybrid_rerank"} onValueChange={(v) => setDraft({ ...draft, config: v as SearchConfig })}>
            <SelectTrigger aria-label="Search configuration"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SEARCH_CONFIGS.map((c) => (
                <SelectItem key={c} value={c}>{c}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </form>

      {!submitted.q && (
        <EmptyState title="Search the bid documents" message="Type a query above. Filter by bid, document type, addendum number or retrieval configuration." />
      )}
      {submitted.q && results.isLoading && <ListSkeleton rows={4} />}
      {results.isError && (
        <ErrorState title="Search failed" message={results.error.message} onRetry={() => void results.refetch()} />
      )}
      {submitted.q && results.data && hits.length === 0 && (
        <EmptyState title="No results" message="Try fewer words, a different document type or no addendum filter." />
      )}
      {hits.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {hits.length} result{hits.length === 1 ? "" : "s"} · {submitted.config}
          </p>
          {hits.map((hit) => (
            <SearchResultCard key={`${hit.chunk.bid_id}:${hit.chunk.chunk_id}`} hit={hit} query={submitted.q} />
          ))}
        </div>
      )}
    </div>
  );
}
