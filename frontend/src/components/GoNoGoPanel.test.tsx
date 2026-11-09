import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { GoNoGoReport } from "@/api/types";
import { GoNoGoPanel } from "@/components/GoNoGoPanel";
import { renderWithProviders, sampleCitation } from "@/test/utils";

const report: GoNoGoReport = {
  bid_id: "sample",
  recommendation: "review",
  summary: "Two criteria are met; the bond requirement could not be confirmed.",
  met: 2,
  not_met: 1,
  unknown: 1,
  generated_at: "2026-10-11T10:00:00Z",
  run_id: "run-gng-1",
  criteria: [
    { criterion: "sealed_bid", label: "Sealed bid accepted", status: "met", critical: false, reason: "Sealed bids are accepted.", fields: ["Bid Submission Type"], sources: [sampleCitation] },
    { criterion: "term", label: "Term under 3 years", status: "met", critical: false, reason: "One year term.", fields: ["Term of Bid"], sources: [] },
    { criterion: "insurance", label: "Insurance within limits", status: "not_met", critical: true, reason: "Requires a $5M umbrella.", fields: ["Bid Bond Requirement"], sources: [] },
    { criterion: "bond", label: "Bid bond affordable", status: "unknown", critical: false, reason: "Not found in documents", fields: [], sources: [] },
  ],
};

describe("GoNoGoPanel", () => {
  it("shows the run button and hint when there is no assessment", async () => {
    const user = userEvent.setup();
    const onRun = vi.fn();
    renderWithProviders(<GoNoGoPanel bidId="sample" report={null} onRun={onRun} running={false} />);
    expect(screen.getByText("Judged against config/capabilities.yaml")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /run assessment/i }));
    expect(onRun).toHaveBeenCalledTimes(1);
  });

  it("renders the recommendation, the counts and one status per criterion", () => {
    renderWithProviders(<GoNoGoPanel bidId="sample" report={report} onRun={() => {}} running={false} />);

    expect(screen.getByText("Needs review")).toHaveAttribute("data-recommendation", "review");
    expect(screen.getByText("2 met · 1 not met · 1 unknown")).toBeInTheDocument();
    expect(screen.getByText(report.summary)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /re-run assessment/i })).toBeEnabled();

    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);
    expect(within(rows[0]).getByText("met")).toHaveAttribute("data-status", "met");
    expect(within(rows[2]).getByText("not met")).toHaveAttribute("data-status", "not_met");
    expect(within(rows[2]).getByText("critical")).toBeInTheDocument();
    expect(within(rows[3]).getByText("unknown")).toHaveAttribute("data-status", "unknown");
    expect(within(rows[0]).getByText("Bid Submission Type")).toBeInTheDocument();
  });

  it("labels go and no-go and disables the button while running", () => {
    const { unmount } = renderWithProviders(
      <GoNoGoPanel bidId="sample" report={{ ...report, recommendation: "go" }} onRun={() => {}} running />,
    );
    expect(screen.getByText("Go")).toHaveAttribute("data-recommendation", "go");
    expect(screen.getByRole("button", { name: /assessing/i })).toBeDisabled();
    unmount();

    renderWithProviders(<GoNoGoPanel bidId="sample" report={{ ...report, recommendation: "no_go" }} onRun={() => {}} running={false} />);
    expect(screen.getByText("No-go")).toHaveAttribute("data-recommendation", "no_go");
  });

  it("opens the citation drawer from a criterion chip", async () => {
    const user = userEvent.setup();
    renderWithProviders(<GoNoGoPanel bidId="sample" report={report} onRun={() => {}} running={false} />);
    await user.click(screen.getByRole("button", { name: /sample-rfp\.pdf p\.7/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Sealed bid accepted");
    expect(dialog).toHaveTextContent(sampleCitation.quote);
  });
});
