import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FieldsTable } from "@/components/FieldsTable";
import { makeField, renderWithProviders } from "@/test/utils";

describe("FieldsTable", () => {
  it("renders a status badge for ok, not_found and failed rows", () => {
    renderWithProviders(
      <FieldsTable
        bidId="sample"
        fields={{
          "Field A": makeField({ status: "ok" }),
          "Field B": makeField({ status: "not_found", value: null, sources: [], confidence: 0.5, notes: "Not found in documents" }),
          "Field C": makeField({ status: "failed", confidence: 0.2 }),
        }}
      />,
    );

    const rowA = screen.getByRole("button", { name: /citations for Field A/i });
    const rowB = screen.getByRole("button", { name: /citations for Field B/i });
    const rowC = screen.getByRole("button", { name: /citations for Field C/i });

    expect(within(rowA).getByText("ok")).toHaveAttribute("data-status", "ok");
    expect(within(rowB).getByText("not found")).toHaveAttribute("data-status", "not_found");
    expect(within(rowC).getByText("failed")).toHaveAttribute("data-status", "failed");
    expect(within(rowB).getByText("Not found in documents")).toBeInTheDocument();
  });

  it("joins list values", () => {
    renderWithProviders(
      <FieldsTable bidId="sample" fields={{ Items: makeField({ value: ["one", "two"] }) }} />,
    );
    expect(screen.getByText("one; two")).toBeInTheDocument();
  });
});
