import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, fileUrl, request, toQuery } from "@/api/client";

function mockFetch(status: number, body: unknown, ok = status < 400) {
  const res = {
    ok,
    status,
    statusText: "Error",
    json: () => Promise.resolve(body),
  } as Response;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(res));
}

describe("client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("throws the API error message", async () => {
    mockFetch(409, { error: "A run is already in progress", run_id: "run-1" });
    const err = await api.extract("sample").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("A run is already in progress");
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).runId).toBe("run-1");
  });

  it("falls back to the status text when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Internal Server Error", json: () => Promise.reject(new Error("x")) }),
    );
    await expect(request("/x")).rejects.toThrow("Internal Server Error");
  });

  it("returns parsed JSON on success", async () => {
    mockFetch(200, { status: "ok", indexed_bids: [] });
    await expect(api.health()).resolves.toEqual({ status: "ok", indexed_bids: [] });
  });

  it("removes a bid with DELETE", async () => {
    mockFetch(200, { bid_id: "BidX", indexed: true, outputs_removed: 2, folder_removed: true });
    await expect(api.removeBid("Bid X")).resolves.toMatchObject({ bid_id: "BidX" });
    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(call[0]).toMatch(/\/bids\/Bid%20X$/);
    expect(call[1].method).toBe("DELETE");
  });

  it("builds query strings and file URLs", () => {
    expect(toQuery({ q: "bond", top_k: 5, doc_type: undefined, config: "" })).toBe("?q=bond&top_k=5");
    expect(fileUrl("b", "a b.pdf", 3)).toMatch(/\/files\/b\/a%20b\.pdf#page=3$/);
    expect(fileUrl("b", "page.html", 1)).toMatch(/\/files\/b\/page\.html$/);
  });
});
