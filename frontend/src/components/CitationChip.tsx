import type { Citation } from "@/api/types";
import { citationLabel, cn } from "@/lib/utils";

interface CitationChipProps {
  citation: Citation;
  index?: number;
  active?: boolean;
  onClick?: () => void;
  className?: string;
}

/** Small "file p.N" chip; clickable when onClick is provided. */
export function CitationChip({ citation, index, active, onClick, className }: CitationChipProps) {
  const label = citationLabel(citation);
  const classes = cn(
    "inline-flex max-w-full items-center gap-1 truncate rounded-md border px-1.5 py-0.5 font-mono text-[11px] sm:max-w-[16rem]",
    active ? "border-primary bg-accent text-accent-foreground" : "bg-muted/60 text-muted-foreground",
    onClick && "hover:border-primary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    className,
  );
  const content = (
    <>
      {index !== undefined && <span className="font-semibold">[{index}]</span>}
      <span className="truncate">{label}</span>
    </>
  );
  if (!onClick) return <span className={classes}>{content}</span>;
  // Chips sit inside clickable rows/cards: stop the click so the parent's
  // handler does not re-open the drawer without the active citation.
  return (
    <button
      type="button"
      className={classes}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={citation.quote}
    >
      {content}
    </button>
  );
}
