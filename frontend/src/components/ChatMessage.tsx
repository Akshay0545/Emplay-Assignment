import ReactMarkdown from "react-markdown";
import { Bot, User } from "lucide-react";
import type { Answer } from "@/api/types";
import { CitationChip } from "@/components/CitationChip";
import { Badge } from "@/components/ui/badge";
import { useCitationDrawer } from "@/hooks/useCitationDrawer";
import { cn, confidenceLabel } from "@/lib/utils";

export type ChatEntry =
  | { kind: "question"; id: string; text: string }
  | { kind: "answer"; id: string; answer: Answer }
  | { kind: "error"; id: string; text: string };

const MARKER = /\[(\d+)\]/g;

/** Replace "[n]" markers in a text node with clickable citation buttons. */
function withMarkers(text: string, onMarker: (n: number) => void, max: number): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(MARKER)) {
    const n = Number(m[1]);
    const idx = m.index ?? 0;
    out.push(text.slice(last, idx));
    if (n >= 1 && n <= max) {
      out.push(
        <button
          key={`${idx}-${n}`}
          type="button"
          className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded bg-accent px-1 align-baseline font-mono text-[11px] font-semibold text-accent-foreground hover:bg-primary hover:text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Open citation ${n}`}
          onClick={() => onMarker(n)}
        >
          [{n}]
        </button>,
      );
    } else {
      out.push(m[0]);
    }
    last = idx + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

function Markers({ children, onMarker, max }: { children: React.ReactNode; onMarker: (n: number) => void; max: number }) {
  if (typeof children === "string") return <>{withMarkers(children, onMarker, max)}</>;
  if (Array.isArray(children)) {
    return (
      <>
        {children.map((c, i) =>
          typeof c === "string" ? <span key={i}>{withMarkers(c, onMarker, max)}</span> : c,
        )}
      </>
    );
  }
  return <>{children}</>;
}

function confidenceBadgeClass(confidence: number): string {
  const label = confidenceLabel(confidence);
  if (label === "high") return "border-green-500 text-green-700 dark:text-green-400";
  if (label === "medium") return "border-amber-500 text-amber-700 dark:text-amber-400";
  return "border-red-500 text-red-700 dark:text-red-400";
}

/** One chat bubble: question, cited answer or error. */
export function ChatMessage({ entry }: { entry: ChatEntry }) {
  const { open } = useCitationDrawer();

  if (entry.kind === "question") {
    return (
      <div className="flex justify-end gap-2">
        <div className="max-w-[88%] [overflow-wrap:anywhere] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground sm:max-w-[80%] sm:px-4">{entry.text}</div>
        <Avatar icon={<User className="h-4 w-4" />} />
      </div>
    );
  }

  if (entry.kind === "error") {
    return (
      <div className="flex gap-2">
        <Avatar icon={<Bot className="h-4 w-4" />} />
        <div className="max-w-[88%] [overflow-wrap:anywhere] rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm sm:max-w-[80%] sm:px-4">{entry.text}</div>
      </div>
    );
  }

  const { answer } = entry;
  // With several bids in scope a Citation carries no bid, so the drawer
  // resolves it from the chunk (bidId null => lookup via /chunks/{id}).
  const bidId = answer.bids.length === 1 ? answer.bids[0] : null;
  const openCitation = (n: number) =>
    open({ title: answer.question, bidId, citations: answer.citations, activeIndex: n - 1 });

  const max = answer.citations.length;
  const markerProps = { onMarker: openCitation, max };

  return (
    <div className="flex gap-2">
      <Avatar icon={<Bot className="h-4 w-4" />} />
      <div className="min-w-0 max-w-[92%] space-y-3 rounded-lg border bg-card px-3 py-3 text-sm sm:max-w-[85%] sm:px-4">
        <div className="[overflow-wrap:anywhere] space-y-1.5 leading-relaxed [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold [&_h3]:font-semibold [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs [&_table]:block [&_table]:overflow-x-auto [&_table]:text-xs [&_td]:border [&_td]:px-1.5 [&_th]:border [&_th]:px-1.5">
          <ReactMarkdown
            components={{
              p: ({ children }) => <p><Markers {...markerProps}>{children}</Markers></p>,
              li: ({ children }) => <li><Markers {...markerProps}>{children}</Markers></li>,
              strong: ({ children }) => <strong><Markers {...markerProps}>{children}</Markers></strong>,
              em: ({ children }) => <em><Markers {...markerProps}>{children}</Markers></em>,
            }}
          >
            {answer.answer}
          </ReactMarkdown>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 border-t pt-2">
          <Badge variant="outline" className={cn("mr-1", confidenceBadgeClass(answer.confidence))}>
            confidence {Math.round(answer.confidence * 100)}%
          </Badge>
          {answer.cached && (
            <Badge variant="outline" className="text-muted-foreground" title="Served from the answer cache">
              cached
            </Badge>
          )}
          {answer.bids.map((b) => (
            <Badge key={b} variant="secondary">{b}</Badge>
          ))}
          {answer.citations.map((c, i) => (
            <CitationChip key={`${c.chunk_id}-${i}`} citation={c} index={i + 1} onClick={() => openCitation(i + 1)} />
          ))}
        </div>
      </div>
    </div>
  );
}

function Avatar({ icon }: { icon: React.ReactNode }) {
  return (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
      {icon}
    </div>
  );
}
