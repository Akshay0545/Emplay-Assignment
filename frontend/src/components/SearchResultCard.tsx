import { FileText } from "lucide-react";
import type { SearchHit } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { useCitationDrawer } from "@/hooks/useCitationDrawer";
import { docTypeLabel, queryTerms } from "@/lib/utils";

const MAX_SNIPPET = 600;

/** Wrap query terms in <mark>. */
export function Highlighted({ text, query }: { text: string; query: string }) {
  const terms = queryTerms(query);
  if (terms.length === 0) return <>{text}</>;
  const pattern = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>,
      )}
    </>
  );
}

/** One search hit: file, page, type, score, ranks and highlighted text. */
export function SearchResultCard({ hit, query }: { hit: SearchHit; query: string }) {
  const { open } = useCitationDrawer();
  const { chunk } = hit;
  const snippet = chunk.text.length > MAX_SNIPPET ? `${chunk.text.slice(0, MAX_SNIPPET)}...` : chunk.text;

  const openDrawer = () =>
    open({
      title: chunk.header || chunk.file_name,
      bidId: chunk.bid_id,
      citations: [
        { file: chunk.file_name, page: chunk.page, chunk_id: chunk.chunk_id, quote: chunk.text.slice(0, 300) },
      ],
      notes: chunk.section ? `Section: ${chunk.section}` : undefined,
    });

  return (
    <Card
      role="button"
      tabIndex={0}
      aria-label={`Open source ${chunk.file_name} page ${chunk.page}`}
      className="cursor-pointer transition-colors hover:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={openDrawer}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openDrawer();
        }
      }}
    >
      <CardContent className="space-y-2 p-3 sm:p-4">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="flex min-w-0 items-center gap-1 font-mono">
            <FileText className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate" title={chunk.file_name}>{chunk.file_name}</span>
          </span>
          <span>p.{chunk.page}</span>
          <Badge variant="secondary">{docTypeLabel(chunk.doc_type)}</Badge>
          {chunk.addendum_number !== null && <Badge variant="outline">Addendum {chunk.addendum_number}</Badge>}
          <Badge variant="outline">{chunk.bid_id}</Badge>
          <span className="ml-auto tabular-nums">score {hit.score.toFixed(3)}</span>
        </div>
        <p className="whitespace-pre-wrap text-sm leading-relaxed">
          <Highlighted text={snippet} query={query} />
        </p>
        {Object.keys(hit.ranks).length > 0 && (
          <p className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            {Object.entries(hit.ranks).map(([name, rank]) => (
              <span key={name} className="rounded bg-muted px-1.5 py-0.5 font-mono">
                {name} #{rank}
              </span>
            ))}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
