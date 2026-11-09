/**
 * Typed fetch wrapper for the RFP Intelligence API.
 * Every failure is thrown as an ApiError carrying the server's `error` message.
 */
import type {
  Answer,
  ApiErrorBody,
  AskRequest,
  BidDetail,
  BidRecord,
  BidSummary,
  Chunk,
  CompareRequest,
  ComparisonReport,
  ExtractionEval,
  ExtractResponse,
  GoNoGoReport,
  Health,
  IndexStarted,
  IndexStats,
  RemoveResult,
  RetrievalEval,
  RunInfo,
  RunStatus,
  SearchParams,
  SearchResponse,
} from "./types";

export const API_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

/** Error raised for any non-2xx response or network failure. */
export class ApiError extends Error {
  readonly status: number;
  readonly runId: string | null;

  constructor(message: string, status: number, runId: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.runId = runId;
  }
}

/** Read the `{error, run_id}` body if present, else fall back to status text. */
async function toApiError(res: Response): Promise<ApiError> {
  let message = res.statusText || `Request failed (${res.status})`;
  let runId: string | null = null;
  try {
    const body = (await res.json()) as Partial<ApiErrorBody> & { detail?: unknown };
    if (typeof body.error === "string") message = body.error;
    else if (typeof body.detail === "string") message = body.detail;
    if (typeof body.run_id === "string") runId = body.run_id;
  } catch {
    // body was not JSON; keep the status text
  }
  return new ApiError(message, res.status, runId);
}

/** Perform a request and parse the JSON response. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch (err) {
    const reason = err instanceof Error ? err.message : "Network error";
    throw new ApiError(`Cannot reach the API at ${API_URL} (${reason})`, 0);
  }
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** Build a query string, skipping undefined/empty values. */
export function toQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** URL of an original bid file served by the API. */
export function fileUrl(bidId: string, fileName: string, page?: number): string {
  const base = `${API_URL}/files/${encodeURIComponent(bidId)}/${encodeURIComponent(fileName)}`;
  const isPdf = fileName.toLowerCase().endsWith(".pdf");
  return isPdf && page ? `${base}#page=${page}` : base;
}

export const api = {
  health: () => request<Health>("/health"),
  listBids: () => request<BidSummary[]>("/bids"),
  getBid: (bidId: string) => request<BidDetail>(`/bids/${encodeURIComponent(bidId)}`),
  getRecord: (bidId: string) =>
    request<BidRecord>(`/bids/${encodeURIComponent(bidId)}/record`),
  uploadBid: (bidId: string, files: File[]) => {
    const form = new FormData();
    form.append("bid_id", bidId);
    for (const file of files) form.append("files", file, file.name);
    return request<IndexStarted>("/bids", { method: "POST", body: form });
  },
  /** Remove a bid from the index and delete its outputs and uploaded files. */
  removeBid: (bidId: string) =>
    request<RemoveResult>(`/bids/${encodeURIComponent(bidId)}`, { method: "DELETE" }),
  index: (folder: string, force = false) =>
    request<IndexStats>("/index", jsonInit("POST", { folder, force })),
  extract: (bidId: string) =>
    request<ExtractResponse>("/extract", jsonInit("POST", { bid_id: bidId })),
  getRun: (runId: string) => request<RunStatus>(`/runs/${encodeURIComponent(runId)}`),
  /** Ask a running run to stop at its next step; 409 when it already finished. */
  cancelRun: (runId: string) =>
    request<RunInfo>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),
  listRuns: () => request<RunInfo[]>("/runs"),
  search: (params: SearchParams) =>
    request<SearchResponse>(`/search${toQuery({ ...params })}`),
  ask: (body: AskRequest) => request<Answer>("/ask", jsonInit("POST", body)),
  /** Start answering in the background; poll `getRun` for progress and the answer. */
  startAsk: (body: AskRequest) => request<ExtractResponse>("/ask/runs", jsonInit("POST", body)),
  getChunk: (chunkId: string) =>
    request<Chunk>(`/chunks/${encodeURIComponent(chunkId)}`),
  evalRetrieval: () => request<RetrievalEval>("/eval/retrieval"),
  evalExtraction: () => request<ExtractionEval>("/eval/extraction"),
  /** Run the comparison agent over two or more bids (10-30 s). */
  compare: (body: CompareRequest) => request<ComparisonReport>("/compare", jsonInit("POST", body)),
  /** Latest saved comparison; 404 when none was run yet. */
  getComparison: () => request<ComparisonReport>("/compare"),
  /** Run the go / no-go agent for a bid (10-20 s). */
  runGoNoGo: (bidId: string) =>
    request<GoNoGoReport>(`/bids/${encodeURIComponent(bidId)}/go-no-go`, { method: "POST" }),
  /** Saved go / no-go assessment; 404 when none was run yet. */
  getGoNoGo: (bidId: string) =>
    request<GoNoGoReport>(`/bids/${encodeURIComponent(bidId)}/go-no-go`),
};
