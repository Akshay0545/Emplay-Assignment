import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Layout } from "@/components/Layout";
import { TooltipProvider } from "@/components/ui/tooltip";

function renderLayout() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={["/"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<p>bids page</p>} />
              <Route path="/search" element={<p>search page</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("Layout", () => {
  it("opens the mobile navigation drawer and closes it after navigating", async () => {
    const user = userEvent.setup();
    renderLayout();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /open navigation/i }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("RFP Intelligence");
    await user.click(screen.getAllByRole("link", { name: /search/i }).at(-1)!);

    expect(await screen.findByText("search page")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
