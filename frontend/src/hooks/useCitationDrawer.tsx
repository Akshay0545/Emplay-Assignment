import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { Citation } from "@/api/types";

/** What the citation drawer shows: one or more citations for a named thing. */
export interface DrawerPayload {
  title: string;
  /** Bid the citations belong to; null when unknown (resolved per chunk). */
  bidId: string | null;
  citations: Citation[];
  notes?: string;
  /** Index of the citation to scroll to / highlight first. */
  activeIndex?: number;
}

interface DrawerContextValue {
  payload: DrawerPayload | null;
  open: (payload: DrawerPayload) => void;
  close: () => void;
}

const DrawerContext = createContext<DrawerContextValue | null>(null);

/** Holds the state of the single app-wide citation drawer. */
export function CitationDrawerProvider({ children }: { children: React.ReactNode }) {
  const [payload, setPayload] = useState<DrawerPayload | null>(null);
  const open = useCallback((p: DrawerPayload) => setPayload(p), []);
  const close = useCallback(() => setPayload(null), []);
  const value = useMemo(() => ({ payload, open, close }), [payload, open, close]);
  return <DrawerContext.Provider value={value}>{children}</DrawerContext.Provider>;
}

/** Open or close the citation drawer from anywhere in the tree. */
// eslint-disable-next-line react-refresh/only-export-components -- provider and hook share one context
export function useCitationDrawer(): DrawerContextValue {
  const ctx = useContext(DrawerContext);
  if (!ctx) throw new Error("useCitationDrawer must be used inside CitationDrawerProvider");
  return ctx;
}
