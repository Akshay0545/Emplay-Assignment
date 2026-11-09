import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { FieldsTable } from "@/components/FieldsTable";
import { makeField, renderWithProviders, sampleCitation } from "@/test/utils";

describe("CitationDrawer", () => {
  it("opens with the quote and page when a row is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <FieldsTable bidId="sample" fields={{ "Due Date": makeField({ notes: "From addendum" }) }} />,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /citations for Due Date/i }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Due Date");
    expect(dialog).toHaveTextContent(sampleCitation.quote);
    expect(dialog).toHaveTextContent("page 7");
    expect(dialog).toHaveTextContent("From addendum");

    const link = screen.getByRole("link", { name: /open pdf at page 7/i });
    expect(link).toHaveAttribute("href", expect.stringMatching(/\/files\/sample\/sample-rfp\.pdf#page=7$/));
  });
});
