import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceBar } from "@/components/ConfidenceBar";

describe("ConfidenceBar", () => {
  it.each([
    [0.95, "bg-green-500"],
    [0.8, "bg-green-500"],
    [0.65, "bg-amber-500"],
    [0.5, "bg-amber-500"],
    [0.2, "bg-red-500"],
  ])("confidence %s uses %s", (confidence, cls) => {
    const { unmount } = render(<ConfidenceBar confidence={confidence} />);
    expect(screen.getByTestId("confidence-fill")).toHaveClass(cls);
    unmount();
  });

  it("shows the percentage", () => {
    render(<ConfidenceBar confidence={0.42} />);
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "42");
  });
});
