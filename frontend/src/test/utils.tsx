import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CitationDrawer } from "@/components/CitationDrawer";
import { CitationDrawerProvider } from "@/hooks/useCitationDrawer";
import type { Citation, FieldValue } from "@/api/types";

/** Render with the providers the components need (query, router, drawer). */
export function renderWithProviders(ui: React.ReactElement, options?: RenderOptions) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <CitationDrawerProvider>
          {ui}
          <CitationDrawer />
        </CitationDrawerProvider>
      </MemoryRouter>
    </QueryClientProvider>,
    options,
  );
}

export const sampleCitation: Citation = {
  file: "sample-rfp.pdf",
  page: 7,
  chunk_id: "abc123:7:2",
  quote: "Sealed bids will be received until 2:00 PM local time.",
};

export function makeField(overrides: Partial<FieldValue> = {}): FieldValue {
  return {
    value: "Sample value",
    sources: [sampleCitation],
    confidence: 0.9,
    notes: "",
    status: "ok",
    ...overrides,
  };
}
