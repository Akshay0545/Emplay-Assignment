import type { FieldStatus } from "@/api/types";
import { cn, statusClasses } from "@/lib/utils";

const LABELS: Record<FieldStatus, string> = {
  ok: "ok",
  not_found: "not found",
  failed: "failed",
};

/** Coloured pill for a field status: ok = green, not_found = gray, failed = red. */
export function StatusBadge({ status, className }: { status: FieldStatus; className?: string }) {
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium",
        statusClasses(status),
        className,
      )}
    >
      {LABELS[status] ?? status}
    </span>
  );
}
