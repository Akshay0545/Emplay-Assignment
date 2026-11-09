import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Loader2, Send, Sparkles } from "lucide-react";
import { ChatMessage, type ChatEntry } from "@/components/ChatMessage";
import { EmptyState } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAsk } from "@/hooks/useAsk";
import { useBids } from "@/hooks/useBids";
import { SUGGESTED_QUESTIONS } from "@/lib/questions";
import { cn } from "@/lib/utils";

let counter = 0;
const nextId = () => `m${++counter}`;

/** Ask page: chat over one or more bids with cited answers. */
export function AskPage() {
  const bids = useBids();
  const ask = useAsk();
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries.length, ask.pending, ask.label]);

  const toggleBid = (id: string) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const submit = (text: string) => {
    const q = text.trim();
    if (!q || ask.pending) return;
    setEntries((prev) => [...prev, { kind: "question", id: nextId(), text: q }]);
    setQuestion("");
    ask.submit(
      { question: q, bid_ids: selected.length ? selected : undefined },
      {
        onAnswer: (answer) => setEntries((prev) => [...prev, { kind: "answer", id: nextId(), answer }]),
        onError: (message) => setEntries((prev) => [...prev, { kind: "error", id: nextId(), text: message }]),
      },
    );
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(question);
  };

  return (
    <div className="flex h-[calc(100dvh-5.5rem)] flex-col gap-3 sm:h-[calc(100dvh-7.5rem)] sm:gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Ask</h1>
          <p className="text-sm text-muted-foreground">Every answer cites the exact passages it was built from.</p>
        </div>
        <fieldset className="flex flex-wrap items-center gap-1.5 sm:justify-end">
          <legend className="sr-only">Bids in scope</legend>
          <span className="mr-1 text-xs text-muted-foreground">Scope:</span>
          {(bids.data ?? []).map((b) => {
            const on = selected.includes(b.bid_id);
            return (
              <button
                key={b.bid_id}
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => toggleBid(b.bid_id)}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 font-mono text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  on ? "border-primary bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {b.bid_id}
              </button>
            );
          })}
          {bids.data && bids.data.length > 0 && (
            <Badge variant="outline" className="font-normal text-muted-foreground">
              {selected.length === 0 ? "all bids" : `${selected.length} selected`}
            </Badge>
          )}
        </fieldset>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto rounded-lg border bg-muted/30 p-3 sm:p-4">
        {entries.length === 0 && (
          <EmptyState
            title="Ask anything about the bids"
            message="Pick a suggested question or type your own. Leave the scope empty to search every bid."
          />
        )}
        {entries.map((entry) => (
          <ChatMessage key={entry.id} entry={entry} />
        ))}
        {ask.pending && (
          <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>{ask.label}...</span>
            <span className="font-mono text-xs tabular-nums">{ask.elapsedS}s</span>
            {ask.runId && (
              <Link
                to={`/runs/${encodeURIComponent(ask.runId)}`}
                className="text-xs underline-offset-4 hover:underline"
              >
                view trace
              </Link>
            )}
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="space-y-2">
        <div className="-mx-4 flex gap-1.5 scroll-x px-4 pb-1 no-scrollbar sm:mx-0 sm:flex-wrap sm:px-0 sm:pb-0" aria-label="Suggested questions">
          {SUGGESTED_QUESTIONS.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => submit(q)}
              disabled={ask.pending}
              className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border bg-card px-3 py-1 text-xs hover:border-primary/60 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:whitespace-normal"
            >
              <Sparkles className="h-3 w-3 shrink-0 text-primary" /> {q}
            </button>
          ))}
        </div>
        <form onSubmit={onSubmit} className="flex gap-2">
          <Input
            aria-label="Question"
            placeholder="Ask a question about the bids..."
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={ask.pending}
          />
          <Button type="submit" disabled={!question.trim() || ask.pending} aria-label="Ask">
            <Send /> <span className="hidden sm:inline">Ask</span>
          </Button>
        </form>
      </div>
    </div>
  );
}
