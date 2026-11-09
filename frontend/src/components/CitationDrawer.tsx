import { useQuery } from "@tanstack/react-query";
import { ExternalLink, FileText } from "lucide-react";
import { api, fileUrl } from "@/api/client";
import type { Citation } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useCitationDrawer, type DrawerPayload } from "@/hooks/useCitationDrawer";
import { cn } from "@/lib/utils";

function isPdf(file: string): boolean {
  return file.toLowerCase().endsWith(".pdf");
}

/** "Open PDF at page" / "Open file" link; resolves the bid from the chunk when unknown. */
function OpenFileLink({ citation, bidId }: { citation: Citation; bidId: string | null }) {
  const chunk = useQuery({
    queryKey: ["chunk", citation.chunk_id],
    queryFn: () => api.getChunk(citation.chunk_id),
    enabled: bidId === null,
    staleTime: Infinity,
  });
  const resolved = bidId ?? chunk.data?.bid_id ?? null;
  const pdf = isPdf(citation.file);
  const label = pdf ? `Open PDF at page ${citation.page}` : "Open file";
  if (!resolved) {
    return (
      <Button size="sm" variant="outline" disabled title={chunk.isError ? "Could not resolve the source bid" : "Resolving..."}>
        <ExternalLink />
        {label}
      </Button>
    );
  }
  return (
    <Button asChild size="sm" variant="outline">
      <a href={fileUrl(resolved, citation.file, citation.page)} target="_blank" rel="noreferrer">
        <ExternalLink />
        {label}
      </a>
    </Button>
  );
}

function CitationCard({
  citation,
  index,
  bidId,
  active,
}: {
  citation: Citation;
  index: number;
  bidId: string | null;
  active: boolean;
}) {
  return (
    <li
      className={cn("rounded-lg border p-3 sm:p-4", active && "border-primary ring-1 ring-primary")}
      data-testid="citation-card"
    >
      <div className="mb-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5 truncate">
          <FileText className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate font-mono" title={citation.file}>
            [{index + 1}] {citation.file}
          </span>
        </span>
        <span className="shrink-0">page {citation.page}</span>
      </div>
      <blockquote className="border-l-2 border-primary/50 pl-3 text-sm leading-relaxed">
        {citation.quote || <span className="italic text-muted-foreground">No quote</span>}
      </blockquote>
      <div className="mt-3 flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span className="max-w-full truncate font-mono text-[11px] text-muted-foreground" title={citation.chunk_id}>
          {citation.chunk_id}
        </span>
        <OpenFileLink citation={citation} bidId={bidId} />
      </div>
    </li>
  );
}

/** Body of the drawer, separated so tests can render it without the sheet. */
export function CitationDrawerBody({ payload }: { payload: DrawerPayload }) {
  const count = payload.citations.length;
  return (
    <>
      <SheetHeader>
        <SheetTitle>{payload.title}</SheetTitle>
        <SheetDescription>
          {count === 0
            ? "No citations for this item."
            : `${count} source${count === 1 ? "" : "s"}${payload.bidId ? ` in ${payload.bidId}` : ""}`}
        </SheetDescription>
      </SheetHeader>
      {payload.notes && (
        <div className="rounded-md bg-muted p-3 text-sm">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Notes</p>
          <p className="whitespace-pre-wrap">{payload.notes}</p>
        </div>
      )}
      <ol className="space-y-3">
        {payload.citations.map((c, i) => (
          <CitationCard
            key={`${c.chunk_id}-${i}`}
            citation={c}
            index={i}
            bidId={payload.bidId}
            active={payload.activeIndex === i}
          />
        ))}
      </ol>
    </>
  );
}

/** Right-side drawer showing the citations of the currently selected item. */
export function CitationDrawer() {
  const { payload, close } = useCitationDrawer();
  return (
    <Sheet open={payload !== null} onOpenChange={(open) => !open && close()}>
      <SheetContent side="right">
        {payload && <CitationDrawerBody payload={payload} />}
      </SheetContent>
    </Sheet>
  );
}
