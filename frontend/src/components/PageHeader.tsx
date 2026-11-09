import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  /** Small line rendered above the title (breadcrumb / back link). */
  eyebrow?: ReactNode;
  /** Right-aligned actions on wide screens; stacked full-width on phones. */
  actions?: ReactNode;
  className?: string;
}

/** Responsive page heading: stacks on phones, title + actions side by side from sm up. */
export function PageHeader({ title, description, eyebrow, actions, className }: PageHeaderProps) {
  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4", className)}>
      <div className="min-w-0 space-y-1">
        {eyebrow}
        <h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
        {description && <div className="text-sm text-muted-foreground">{description}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2 [&>*]:w-full sm:[&>*]:w-auto">{actions}</div>}
    </div>
  );
}
