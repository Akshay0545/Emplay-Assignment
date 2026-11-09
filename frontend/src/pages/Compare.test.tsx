import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BidSummary, Citation, ComparisonReport } from "@/api/types";

vi.mock("@/api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/api/client")>();
  return { ...mod, api: { ...mod.api, listBids: vi.fn(), getComparison: vi.fn(), compare: vi.fn() } };
});

import { api, ApiError } from "@/api/client";
import { ComparePage } from "@/pages/Compare";
import { renderWithProviders, sampleCitation } from "@/test/utils";

const bid = (bid_id: string): BidSummary => ({
  bid_id, title: null, agency: null, due_date: null, due_date_changed_by: null, doc_count: 1, indexed: true, indexing: false, last_run: null, validation: null,
});

const otherCitation: Citation = { file: "other-rfp.pdf", page: 2, chunk_id: "def456:2:1", quote: "Bids are due at noon." };

const report: ComparisonReport = {
  bid_ids: ["A", "B"],
  narrative: "Bid A is due earlier than Bid B.",
  highlights: [{ field: "Due Date", note: "A week apart.", sources: [sampleCitation, otherCitation] }],
  generated_at: "2026-10-11T10:00:00Z",
  run_id: "run-cmp-1",
  rows: [
    {
      field: "Due Date",
      differs: true,
      cells: [
        { bid_id: "A", value: "July 9, 2024", status: "ok", confidence: 0.9, notes: "", sources: [sampleCitation] },
        { bid_id: "B", value: "July 16, 2024", status: "ok", confidence: 0.9, notes: "", sources: [otherCitation] },
      ],
    },
    {
      field: "Bid Submission Type",
      differs: false,
      cells: [
        { bid_id: "A", value: "Sealed", status: "ok", confidence: 0.8, notes: "", sources: [sampleCitation] },
        { bid_id: "B", value: "Sealed", status: "ok", confidence: 0.8, notes: "", sources: [otherCitation] },
      ],
    },
    {
      field: "Pre Bid Meeting",
      differs: true,
      cells: [
        { bid_id: "A", value: null, status: "not_found", confidence: 0.5, notes: "Not found in documents", sources: [] },
        { bid_id: "B", value: ["June 1", "June 2"], status: "ok", confidence: 0.7, notes: "", sources: [otherCitation] },
      ],
    },
  ],
};

describe("ComparePage", () => {
  beforeEach(() => {
    vi.mocked(api.listBids).mockReset().mockResolvedValue([bid("A"), bid("B"), bid("C")]);
    vi.mocked(api.getComparison).mockReset().mockResolvedValue(report);
    vi.mocked(api.compare).mockReset();
  });

  it("pre-selects the first two bids and enables the compare button", async () => {
    renderWithProviders(<ComparePage />);
    expect(await screen.findByRole("checkbox", { name: "A" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "B" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "C" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Compare 2 bids" })).toBeEnabled();
  });

  it("renders the latest report, highlights differing rows and filters to them", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ComparePage />);

    expect(await screen.findByText(report.narrative)).toBeInTheDocument();
    expect(screen.getByText("2 of 3 fields differ. Click a cell to see its sources.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /run run-cmp-1/ })).toHaveAttribute("href", "/runs/run-cmp-1");

    const dueRow = screen.getByText("Due Date", { selector: "td" }).closest("tr");
    const typeRow = screen.getByText("Bid Submission Type").closest("tr");
    expect(dueRow).toHaveAttribute("data-differs", "true");
    expect(typeRow).toHaveAttribute("data-differs", "false");
    expect(screen.getByText("June 1; June 2")).toBeInTheDocument();
    expect(screen.getByText("Not found in documents")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /only differences/i }));
    expect(screen.queryByText("Bid Submission Type")).not.toBeInTheDocument();
    expect(screen.getByText("Pre Bid Meeting")).toBeInTheDocument();
  });

  it("opens the citation drawer with the cell's quote", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ComparePage />);
    const cell = await screen.findByRole("button", { name: /citations for Due Date in B/i });
    expect(within(cell).getByText("ok")).toHaveAttribute("data-status", "ok");
    await user.click(cell);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Due Date (B)");
    expect(dialog).toHaveTextContent(otherCitation.quote);
    expect(dialog).not.toHaveTextContent(sampleCitation.quote);
  });

  it("runs a comparison with the selected bids and shows the new report", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getComparison).mockReset().mockRejectedValue(new ApiError("none", 404));
    vi.mocked(api.compare).mockResolvedValue({ ...report, narrative: "Fresh comparison." });
    renderWithProviders(<ComparePage />);

    expect(await screen.findByText("No comparison yet")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Compare 2 bids" }));
    expect(await screen.findByText("Fresh comparison.")).toBeInTheDocument();
    expect(api.compare).toHaveBeenCalledWith({ bid_ids: ["A", "B"] });
  });
});
