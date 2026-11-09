import { lazy, Suspense } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { CitationDrawer } from "@/components/CitationDrawer";
import { Layout } from "@/components/Layout";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CitationDrawerProvider } from "@/hooks/useCitationDrawer";
import { ThemeProvider } from "@/hooks/useTheme";
import { AskPage } from "@/pages/Ask";
import { BidDetailPage } from "@/pages/BidDetail";
import { BidsPage } from "@/pages/Bids";
import { ComparePage } from "@/pages/Compare";
import { RunTracePage } from "@/pages/RunTrace";
import { SearchPage } from "@/pages/Search";
import { ListSkeleton } from "@/components/States";

// Recharts is only needed on the Evaluation page; load it on demand.
const EvaluationPage = lazy(() => import("@/pages/Evaluation").then((m) => ({ default: m.EvaluationPage })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
  },
});

/** Root component: providers + routes. */
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={300}>
          <CitationDrawerProvider>
            <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
              <Routes>
                <Route element={<Layout />}>
                  <Route path="/" element={<BidsPage />} />
                  <Route path="/bids/:bidId" element={<BidDetailPage />} />
                  <Route path="/search" element={<SearchPage />} />
                  <Route path="/ask" element={<AskPage />} />
                  <Route path="/compare" element={<ComparePage />} />
                  <Route
                    path="/eval"
                    element={
                      <Suspense fallback={<ListSkeleton rows={6} />}>
                        <EvaluationPage />
                      </Suspense>
                    }
                  />
                  <Route path="/runs/:runId" element={<RunTracePage />} />
                </Route>
              </Routes>
              <CitationDrawer />
            </BrowserRouter>
            <Toaster />
          </CitationDrawerProvider>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
