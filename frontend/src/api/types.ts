/**
 * Hand-written mirrors of the Pydantic models in rfp_intel/schemas.py plus the
 * API-only response shapes from BUILD_PLAN Section 6.5. This file is the
 * source of truth for the UI until `npm run types` generates src/api/schema.ts.
 */

export type DocType =
  | "bid_page"
  | "rfp"
  | "addendum"
  | "specs"
  | "affidavit"
  | "form"
  | "other";

export const DOC_TYPES: DocType[] = [
  "bid_page",
  "rfp",
  "addendum",
  "specs",
  "affidavit",
  "form",
  "other",
];

export type SearchConfig = "vector" | "hybrid" | "hybrid_rerank";

export const SEARCH_CONFIGS: SearchConfig[] = ["vector", "hybrid", "hybrid_rerank"];

export type FieldStatus = "ok" | "not_found" | "failed";

export type RunState = "running" | "done" | "failed" | "cancelled";

export interface Citation {
  file: string;
  page: number;
  chunk_id: string;
  quote: string;
}

export interface Chunk {
  chunk_id: string;
  bid_id: string;
  file_name: string;
  doc_type: DocType;
  addendum_number: number | null;
  page: number;
  doc_date: string | null;
  section: string | null;
  kind: "text" | "table" | "qa" | "spec_list" | "record";
  header: string;
  text: string;
  token_count: number;
}

export interface SearchHit {
  chunk: Chunk;
  score: number;
  ranks: Record<string, number>;
}

export interface FieldValue {
  value: string | string[] | null;
  sources: Citation[];
  confidence: number;
  notes: string;
  status: FieldStatus;
}

export interface AddendumChange {
  field: string;
  kind: "override" | "clarification" | "addition";
  old_value: string | null;
  new_value: string;
  addendum_number: number;
  source: Citation;
}

export interface ValidationCounts {
  passed: number;
  failed: number;
  not_found: number;
}

export interface BidRecord {
  bid_id: string;
  fields: Record<string, FieldValue>;
  addendum_changes: AddendumChange[];
  validation: ValidationCounts;
  generated_at: string;
  run_id: string;
}

export interface Answer {
  question: string;
  answer: string;
  citations: Citation[];
  confidence: number;
  bids: string[];
  /** Served from the semantic answer cache. */
  cached: boolean;
}

export interface StepLog {
  run_id: string;
  step: number;
  agent: string;
  node: string;
  started_at: string;
  input_summary: string;
  tool_calls: Record<string, unknown>[];
  output_summary: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_ms: number;
  error: string | null;
}

export type RunKind = "extract" | "qa" | "compare" | "go_no_go";

/** GET /runs/{id}: an extraction, or a question whose `answer` is filled when done. */
export interface RunStatus {
  run_id: string;
  /** Questions: the bids in scope joined with ","; "" when all bids were searched. */
  bid_id: string;
  status: RunState;
  steps: StepLog[];
  kind: RunKind;
  answer: Answer | null;
  error: string | null;
  summary: RunSummary | null;
}

/** Totals over a run's steps (server-side). */
export interface RunSummary {
  steps: number;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  llm_calls: number;
  searches: number;
  errors: number;
}

/** One row of GET /runs: a run this API process started (no steps). */
export interface RunInfo {
  run_id: string;
  bid_id: string;
  status: RunState;
  kind: RunKind;
}

export interface IndexStats {
  bid_id: string;
  documents: number;
  chunks_added: number;
  chunks_total: number;
  skipped: boolean;
  warnings: string[];
  seconds: number;
}

// ---- API-only shapes (Section 6.5) ----

export interface AskQueue {
  running: number;
  queued: number;
  workers: number;
}

export interface Health {
  status: string;
  indexed_bids: string[];
  ask_queue: AskQueue;
  /** Provider LLM calls go to: the configured one, or the fallback once it kicked in. */
  llm_provider: string;
}

export interface BidSummary {
  bid_id: string;
  title: string | null;
  agency: string | null;
  due_date: string | null;
  due_date_changed_by: number | null;
  doc_count: number;
  indexed: boolean;
  /** An upload is still being parsed and embedded. */
  indexing: boolean;
  last_run: string | null;
  validation: ValidationCounts | null;
}

/** POST /bids (202): files are saved, indexing continues in the background. */
export interface IndexStarted {
  bid_id: string;
  run_id: string;
  documents: number;
}

/** DELETE /bids/{bid_id}: what was removed. */
export interface RemoveResult {
  bid_id: string;
  indexed: boolean;
  outputs_removed: number;
  folder_removed: boolean;
}

export interface DocumentSummary {
  file_name: string;
  doc_type: DocType;
  addendum_number: number | null;
  pages: number;
  tables: number;
  warnings: string[];
}

export interface BidDetail {
  bid_id: string;
  documents: DocumentSummary[];
  indexed: boolean;
  chunks: number;
  /** An upload is still being parsed and embedded. */
  indexing: boolean;
}

export interface ExtractResponse {
  run_id: string;
}

export interface SearchResponse {
  hits: SearchHit[];
}

export interface SearchParams {
  q: string;
  bid_id?: string;
  doc_type?: DocType;
  addendum?: number;
  top_k?: number;
  config?: SearchConfig;
}

export interface AskRequest {
  question: string;
  bid_ids?: string[];
}

export type RetrievalMetric = "recall@1" | "recall@3" | "recall@5" | "recall@10" | "mrr";

export type RetrievalSummary = Record<string, Partial<Record<RetrievalMetric, number>>>;

export interface RetrievalQuestionResult {
  id: string;
  question: string;
  bid_id: string;
  expected_file: string;
  key_phrase: string;
  ranks: Record<string, number | null>;
}

export interface RetrievalEval {
  summary: RetrievalSummary;
  questions: RetrievalQuestionResult[];
}

export type ExtractionScore = "exact" | "partial" | "wrong" | "not_scored";

export interface ExtractionFieldResult {
  field: string;
  gold: unknown;
  output: unknown;
  score: ExtractionScore;
  citations?: number;
  counted?: boolean;
  reason?: string;
}

/** One bid in eval/results/extraction.json (written by eval/run_extraction_eval.py). */
export interface ExtractionBidResult {
  bid_id: string;
  fields: ExtractionFieldResult[];
  exact: number;
  partial: number;
  wrong: number;
  scored_fields: number;
  accuracy: number;
  fields_with_citations?: number;
  values_without_citation?: number;
}

export interface ExtractionEval {
  judge: boolean;
  bids: ExtractionBidResult[];
}

// ---- Comparison and go / no-go (cross-bid agents) ----

/** One bid's value of a field inside a comparison row. */
export interface ComparisonCell {
  bid_id: string;
  value: string | string[] | null;
  status: FieldStatus;
  confidence: number;
  notes: string;
  sources: Citation[];
}

export interface ComparisonRow {
  field: string;
  cells: ComparisonCell[];
  /** True when the bids disagree on this field. */
  differs: boolean;
}

export interface ComparisonHighlight {
  field: string;
  note: string;
  sources: Citation[];
}

export interface ComparisonReport {
  bid_ids: string[];
  rows: ComparisonRow[];
  narrative: string;
  highlights: ComparisonHighlight[];
  generated_at: string;
  run_id: string;
}

export interface CompareRequest {
  bid_ids: string[];
}

export type CriterionStatus = "met" | "not_met" | "unknown";

export interface CriterionResult {
  criterion: string;
  label: string;
  status: CriterionStatus;
  critical: boolean;
  reason: string;
  /** Extraction fields the judgement was based on. */
  fields: string[];
  sources: Citation[];
}

export type Recommendation = "go" | "no_go" | "review";

export interface GoNoGoReport {
  bid_id: string;
  recommendation: Recommendation;
  criteria: CriterionResult[];
  summary: string;
  met: number;
  not_met: number;
  unknown: number;
  generated_at: string;
  run_id: string;
}

export interface ApiErrorBody {
  error: string;
  run_id: string | null;
}
