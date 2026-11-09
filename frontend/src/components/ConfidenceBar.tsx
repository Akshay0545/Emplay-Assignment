import { cn, confidenceColor, confidenceLabel } from "@/lib/utils";

interface ConfidenceBarProps {
  confidence: number;
  className?: string;
  showValue?: boolean;
}

/** Horizontal bar coloured green (>= 0.8), amber (0.5-0.8) or red (< 0.5). */
export function ConfidenceBar({ confidence, className, showValue = true }: ConfidenceBarProps) {
  const pct = Math.round(Math.min(1, Math.max(0, confidence)) * 100);
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={`Confidence ${pct}% (${confidenceLabel(confidence)})`}
        className="h-2 w-20 overflow-hidden rounded-full bg-muted"
      >
        <div
          data-testid="confidence-fill"
          className={cn("h-full rounded-full", confidenceColor(confidence))}
          style={{ width: `${pct}%` }}
        />
      </div>
      {showValue && <span className="w-9 text-xs tabular-nums text-muted-foreground">{pct}%</span>}
    </div>
  );
}
