import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Citation, FieldStatus } from "@/api/types";

/** Merge Tailwind class names (shadcn convention). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Render a field value (string, list or null) as display text. */
export function formatValue(value: string | string[] | null | undefined): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join("; ");
  return value;
}

/** Tailwind colour classes for a confidence score (plan Section 6.6 rules). */
export function confidenceColor(confidence: number): string {
  if (confidence >= 0.8) return "bg-green-500";
  if (confidence >= 0.5) return "bg-amber-500";
  return "bg-red-500";
}

/** Label for a confidence badge. */
export function confidenceLabel(confidence: number): "high" | "medium" | "low" {
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.5) return "medium";
  return "low";
}

/** Badge classes for a field status. */
export function statusClasses(status: FieldStatus): string {
  switch (status) {
    case "ok":
      return "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300";
    case "failed":
      return "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300";
    default:
      return "bg-neutral-200 text-neutral-700 dark:bg-neutral-700 dark:text-neutral-200";
  }
}

/** Short label for a citation chip, e.g. "rfp.pdf p.3". */
export function citationLabel(citation: Citation): string {
  return `${citation.file} p.${citation.page}`;
}

/** Format an ISO timestamp for display, tolerating bad input. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/** Split a query into highlightable terms (3+ chars, de-duplicated). */
export function queryTerms(query: string): string[] {
  const terms = query
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length >= 3);
  return Array.from(new Set(terms));
}

/** Human-friendly document type label. */
export function docTypeLabel(docType: string): string {
  return docType.replace(/_/g, " ");
}
