"""All Pydantic models shared between modules and agents (BUILD_PLAN Section 5).

No raw dicts cross module boundaries: ingestion, search, agents, API and CLI
exchange only these models.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DocType(str, Enum):
    """Kind of document inside a bid folder."""

    BID_PAGE = "bid_page"
    RFP = "rfp"
    ADDENDUM = "addendum"
    SPECS = "specs"
    AFFIDAVIT = "affidavit"
    FORM = "form"
    OTHER = "other"


class Page(BaseModel):
    """One parsed page of a document."""

    number: int                          # 1-based; HTML = 1
    text: str
    tables: list[list[list[str]]] = []   # rows x cells
    is_empty: bool = False


class Document(BaseModel):
    """One parsed input file with its metadata."""

    doc_id: str                          # sha1 of file bytes
    bid_id: str                          # folder name
    file_name: str
    doc_type: DocType
    addendum_number: int | None = None
    doc_date: str | None = None          # ISO
    title: str | None = None
    pages: list[Page]
    fields: dict[str, str] = {}          # HTML label/value pairs
    warnings: list[str] = []


class Chunk(BaseModel):
    """A retrievable unit of text with its context metadata."""

    chunk_id: str                        # f"{doc_id}:{page}:{seq}"
    bid_id: str
    file_name: str
    doc_type: DocType
    addendum_number: int | None
    page: int
    doc_date: str | None
    section: str | None
    kind: Literal["text", "table", "qa", "spec_list", "record"]
    header: str                          # "Bid1 | addendum 1 | file | p2 | Q31"
    text: str
    token_count: int


class Citation(BaseModel):
    """A pointer to the exact source text supporting a value."""

    file: str
    page: int
    chunk_id: str
    quote: str                           # exact supporting span, <= 300 chars


class SearchFilters(BaseModel):
    """Metadata filters applied to a search."""

    bid_id: str | None = None
    bid_ids: list[str] | None = None
    doc_types: list[DocType] | None = None
    addendum_number: int | None = None


class IndexStats(BaseModel):
    """Result of indexing one bid folder (used by the plan but not defined there)."""

    bid_id: str
    documents: int
    chunks_added: int
    chunks_total: int
    skipped: bool = False                # True when the bid was unchanged since last index
    warnings: list[str] = []
    seconds: float = 0.0


class IndexStarted(BaseModel):           # POST /bids (202)
    """Files are saved; parsing and embedding continue in the background."""

    bid_id: str
    run_id: str
    documents: int                       # files written to data/<bid_id>


class SearchHit(BaseModel):
    """One search result with its final score and per-retriever ranks."""

    chunk: Chunk
    score: float
    ranks: dict[str, int] = {}           # {"vector": 3, "bm25": 1, "rerank": 1}


# ---- agent-to-agent messages ----
class Task(BaseModel):
    """A unit of work the orchestrator hands to retrieval + extraction."""

    group: str                           # dates_logistics | commercial_legal | product_specs | qa
    fields: list[str] = []
    queries: list[str] = []
    filters: SearchFilters = SearchFilters()


class Plan(BaseModel):
    """The orchestrator's plan for a run."""

    mode: Literal["extract", "qa", "compare", "go_no_go"]
    question_type: Literal["extract", "single", "comparison", "changes"] = "extract"
    tasks: list[Task]


class EvidenceRequest(BaseModel):
    """What an agent asks the Retrieval agent to find."""

    field_or_question: str
    queries: list[str]
    filters: SearchFilters
    top_k: int = 6


class EvidenceBundle(BaseModel):
    """The Retrieval agent's answer to an EvidenceRequest."""

    request: EvidenceRequest
    hits: list[SearchHit]
    expanded_queries: list[str]


class FieldExtraction(BaseModel):        # LLM output per field
    """Raw extraction of one field as returned by the LLM."""

    field: str
    value: str | list[str] | None
    evidence_ids: list[str]              # ["E2", "E5"] -> mapped to Citation in code
    quotes: list[str]
    status: Literal["ok", "not_found"]
    notes: str = ""


class FieldExtractions(BaseModel):
    """LLM output of one extractor call: one FieldExtraction per requested field."""

    items: list[FieldExtraction]


class QAPlan(BaseModel):                 # LLM output of the Orchestrator in question mode
    """How a question should be answered."""

    question_type: Literal["single", "comparison", "changes"]
    bids: list[str] = []
    doc_types: list[DocType] = []
    queries: list[str]


class ReconcileDecision(BaseModel):      # LLM output of the Addendum Reconciler, per field
    """Whether addendum evidence changes a field."""

    field: str
    kind: Literal["override", "clarification", "addition", "none"]
    new_value: str | None
    old_value: str | None                # the value before the addendum, when the evidence or notes state it
    evidence_id: str | None
    quote: str | None
    reason: str = ""


class ReconcileDecisions(BaseModel):
    """One decision per field that had addendum evidence."""

    items: list[ReconcileDecision]


class SupportCheck(BaseModel):           # LLM output of the Validator, per field
    """Is the value fully supported by its cited evidence?"""

    field: str
    supported: bool
    reason: str = ""
    unsupported_items: list[str] = []    # for list values: the items (verbatim) the evidence does not support


class SupportChecks(BaseModel):
    """Support verdicts for a group of fields."""

    items: list[SupportCheck]


class AnswerDraft(BaseModel):            # LLM output of the Q&A agent
    """A cited answer before citations are resolved."""

    answer: str
    found: bool
    evidence_ids: list[str]
    quotes: list[str]


class FieldValue(BaseModel):
    """Final value of one field in the deliverable JSON."""

    value: str | list[str] | None
    sources: list[Citation] = []
    confidence: float = Field(ge=0, le=1)
    notes: str = ""
    status: Literal["ok", "not_found", "failed"] = "ok"


class AddendumChange(BaseModel):
    """One change an addendum made to a field."""

    field: str
    kind: Literal["override", "clarification", "addition"]
    old_value: str | None
    new_value: str
    addendum_number: int
    source: Citation


class ValidationResult(BaseModel):
    """Validator verdict for one field."""

    field: str
    passed: bool
    checks: dict[str, bool]              # has_citation, quote_in_chunk, supported, format_ok, consistent
    reason: str = ""


class BidRecord(BaseModel):              # deliverable JSON (outputs/<bid>.json)
    """The per-bid deliverable: exactly the 20 configured fields."""

    bid_id: str
    fields: dict[str, FieldValue]        # exactly the 20 field names
    addendum_changes: list[AddendumChange] = []
    validation: dict[str, int]           # {"passed", "failed", "not_found"}
    generated_at: str
    run_id: str

    @model_validator(mode="after")
    def _check_field_names(self) -> BidRecord:
        """Require exactly the field names listed in config/fields.yaml."""
        from rfp_intel.config import field_names  # local import avoids a cycle

        expected, got = set(field_names()), set(self.fields)
        if got != expected:
            raise ValueError(
                f"fields must match config/fields.yaml; "
                f"missing={sorted(expected - got)} unexpected={sorted(got - expected)}"
            )
        return self


class Answer(BaseModel):
    """A cited answer to a free-form question."""

    question: str
    answer: str
    citations: list[Citation]
    confidence: float
    bids: list[str]
    cached: bool = False                 # served from the semantic answer cache


# ---- bonus agents: bid comparison and go / no-go ----
class ComparisonCell(BaseModel):
    """One bid's value of one field inside a comparison row."""

    bid_id: str
    value: str | list[str] | None
    status: Literal["ok", "not_found", "failed"]
    confidence: float
    notes: str = ""
    sources: list[Citation] = []


class ComparisonRow(BaseModel):
    """One field across every compared bid."""

    field: str
    cells: list[ComparisonCell]
    differs: bool                        # values are not all equivalent across the bids


class ComparisonHighlight(BaseModel):
    """A notable difference the Comparison agent wrote about, with the cells' citations."""

    field: str
    note: str
    sources: list[Citation] = []


class ComparisonHighlightDraft(BaseModel):
    """One highlight as the LLM returns it (citations are attached in code)."""

    field: str
    note: str


class ComparisonDraft(BaseModel):        # LLM output of the Comparison agent
    """Narrative plus per-field highlights; fields must be names from the table."""

    narrative: str
    highlights: list[ComparisonHighlightDraft]


class ComparisonReport(BaseModel):      # outputs/comparison.json
    """Side-by-side comparison of two or more bids built from their saved records."""

    bid_ids: list[str]
    rows: list[ComparisonRow]
    narrative: str
    highlights: list[ComparisonHighlight] = []
    generated_at: str
    run_id: str


CriterionStatus = Literal["met", "not_met", "unknown"]


class CriterionCheck(BaseModel):         # LLM output of the Go/No-Go agent, per criterion
    """The LLM's verdict for one capability criterion."""

    criterion: str
    status: CriterionStatus
    reason: str = ""


class CriterionChecks(BaseModel):
    """One verdict per criterion."""

    items: list[CriterionCheck]


class CriterionResult(BaseModel):
    """Final verdict for one criterion with the citations of the fields it used."""

    criterion: str
    label: str
    status: CriterionStatus
    critical: bool
    reason: str = ""
    fields: list[str] = []
    sources: list[Citation] = []


class GoNoGoReport(BaseModel):           # outputs/<bid>_go_no_go.json
    """Go / no-go recommendation for one bid against config/capabilities.yaml."""

    bid_id: str
    recommendation: Literal["go", "no_go", "review"]
    criteria: list[CriterionResult]
    summary: str
    met: int
    not_met: int
    unknown: int
    generated_at: str
    run_id: str


class StepLog(BaseModel):                # one line of logs/trace_<run_id>.jsonl
    """Observability record for one graph step."""

    run_id: str
    step: int
    agent: str
    node: str
    started_at: str
    input_summary: str
    tool_calls: list[dict]
    output_summary: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int
    error: str | None = None


RunKind = Literal["extract", "qa", "compare", "go_no_go", "index"]


class RunSummary(BaseModel):
    """Totals over a run's StepLogs."""

    steps: int
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    llm_calls: int
    searches: int
    errors: int


class RunStatus(BaseModel):
    """Progress of a background run: an extraction, or a question (answer filled when done)."""

    run_id: str
    bid_id: str                          # qa runs: the bids in scope joined with ","; "" for all
    status: Literal["running", "done", "failed", "cancelled"]
    steps: list[StepLog]
    kind: RunKind = "extract"
    answer: Answer | None = None
    error: str | None = None
    summary: RunSummary | None = None


class RunInfo(BaseModel):               # GET /runs row
    """A background run known to this API process (no steps)."""

    run_id: str
    bid_id: str
    status: Literal["running", "done", "failed", "cancelled"]
    kind: RunKind = "extract"


class BidSummary(BaseModel):            # GET /bids row (Phase 7a)
    """One row of the bid list: folder facts plus headline values of the saved record."""

    bid_id: str
    title: str | None = None
    agency: str | None = None
    due_date: str | None = None
    due_date_changed_by: int | None = None   # addendum number that overrode "Due Date"
    doc_count: int = 0
    indexed: bool = False
    indexing: bool = False                   # an upload is still being parsed and embedded
    last_run: str | None = None              # generated_at of the saved record
    validation: dict[str, int] | None = None


class DocumentInfo(BaseModel):
    """Per-file facts shown on the bid detail page."""

    file_name: str
    doc_type: DocType
    addendum_number: int | None = None
    pages: int
    tables: int = 0
    warnings: list[str] = []


class RemoveResult(BaseModel):           # DELETE /bids/{bid_id}
    """What removing a bid deleted."""

    bid_id: str
    indexed: bool                        # it had chunks in the index
    outputs_removed: int                 # files deleted under outputs/
    folder_removed: bool


class BidDetail(BaseModel):              # GET /bids/{bid_id}
    """Documents of one bid plus its index state."""

    bid_id: str
    documents: list[DocumentInfo]
    indexed: bool
    chunks: int
    indexing: bool = False               # an upload is still being parsed and embedded


class Usage(BaseModel):
    """Token and cost usage of one or more LLM calls (BUILD_PLAN 6.3)."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        """Sum two usages."""
        return Usage(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            cost_usd=self.cost_usd + other.cost_usd,
        )
