# RFP Intelligence Platform: Build Plan for Claude Code

This file is the complete specification and phase-by-phase build plan.
Claude Code: read the whole file before Phase 0, then build one phase at a
time when asked ("Do Phase N"), following `CLAUDE.md`.

---

## 1. What we are building

A Python system plus a React web app that:

1. **Ingests** a bid folder (PDF + HTML files, including addendums): parses
   text and tables per page, cleans it, and tags metadata.
2. **Indexes** it in a **hybrid RAG search engine**: structure-aware chunks,
   dense vectors (Qdrant) + BM25, Reciprocal Rank Fusion, cross-encoder
   re-ranking, query expansion, metadata filters, citations (file, page,
   text). Usable alone via CLI and REST API.
3. Runs a **LangGraph multi-agent system** (Orchestrator, Ingestion,
   Retrieval, 3 Extractors, Addendum Reconciler, Validator, Q&A/Report) that
   - **extraction mode**: produces one JSON per bid with 20 fields, each with
     value, citations, confidence and notes, plus an addendum change log and
     validation counts;
   - **question mode**: answers free-form questions with citations.
4. Exposes everything through a **CLI**, a **FastAPI** API and a **React**
   web app.

It must work on an **unseen bid folder without code changes**.

### Inputs

- `data/Bid1/` Dallas ISD "Student and Staff Computing Devices": BidNet HTML
  page, main RFP PDF (40 pages), Addendum 1 (Q&A), Addendum 2 (due date
  extension).
- `data/Bid2/` Maryland State Treasurer "Dell Laptops w/Extended Warranty":
  BidNet HTML page, PORFP PDF, Dell Laptop Specs PDF, Contract Affidavit PDF,
  Mercury Affidavit PDF.

### Requirements checklist (from the brief)

Every row must have its proof by the end of Phase 8.

| ID | Requirement | Proof |
|---|---|---|
| A1 | Parse HTML + PDF, keep page structure | test_pdf_parser, test_html_parser |
| A2 | Extract tables correctly | test_chunker (table case) |
| A3 | Clean headers/footers, broken lines, hyphenation, whitespace | test_cleaner |
| A4 | Metadata: bid_id, file_name, doc_type, addendum_number, page, doc date | test_classifier, chunk payloads |
| A5 | Handle scanned/empty/unreadable files gracefully + log (OCR bonus) | test_ingestion_failures |
| B1 | RFP-suited chunking with overlap, justified in README | test_chunker, README |
| B2 | Embeddings | index command |
| B3 | Vector store with metadata | test_search |
| B4 | Incremental indexing | test_incremental |
| B5 | Hybrid search (dense + BM25) merged with RRF | eval config `hybrid` |
| B6 | Metadata filtering ("only search Bid2 specs") | test_search, test_query_expansion |
| B7 | Re-ranking (cross-encoder) | eval config `hybrid_rerank` |
| B8 | Query rewriting / expansion | test_query_expansion |
| B9 | Citations: file, page, chunk text | /search response |
| B10 | REST API + CLI (`POST /index`, `GET /search`, `POST /ask`) | curl checks |
| B11 | >=15 eval questions, Recall@k + MRR, >=2 configs, table in README | eval/results/retrieval.md |
| C1-C7 | Orchestrator, Ingestion, Retrieval, Extraction, Addendum Reconciliation, Validator, Q&A/Report agents | trace |
| C8 | Framework choice explained | README |
| C9 | Shared state object | GraphState |
| C10 | Validator feedback loop with max retries | trace shows a retry |
| C11 | Parallel agents where possible | overlapping timestamps in trace |
| C12 | Structured outputs for all agent messages | test_schemas |
| C13 | Guardrails: "Not found in documents", >=1 citation per value | test_validator_rules |
| C14 | Observability: per-step input, tool calls, output, tokens, latency; one example trace | logs/example_trace.jsonl |
| C15 | Error handling: LLM timeout, bad JSON, empty retrieval | test_error_handling |
| C16 | Extraction mode | python main.py --bid ./data/Bid1 |
| C17 | QA mode incl. the 5 example questions in the brief | eval/qa_log.md |
| D1-D4 | 20 fields; value + sources + confidence + notes; addendum_changes + validation counts; due date with time + time zone after addendums | outputs/*.json |
| T1-T6 | Python 3.10+; keys from env; one command; modular; unit tests; unseen bid works | Phase 8 checks |
| G1-G8 | Repo, README, architecture diagram, JSON per bid, retrieval report, Q&A log (>=10), agent trace, demo video | Phase 8 checks |
| X1-X6 | Bonus: web UI, OCR, comparison agent, go/no-go, semantic cache + cost tracking, Docker + CI | Phases 7 and 9 |

---

## 2. Tech stack

| Area | Choice |
|---|---|
| Language | Python 3.11 (backend), TypeScript (frontend) |
| PDF | PyMuPDF (text per page), pdfplumber (tables) |
| HTML | BeautifulSoup4 + lxml (targeted label/value extraction) |
| OCR (optional) | pytesseract, behind `ingestion.ocr.enabled` |
| Tokens | tiktoken (cl100k_base) |
| Embeddings | sentence-transformers, `BAAI/bge-small-en-v1.5` (local) |
| Vector store | Qdrant embedded local mode (`qdrant-client`, path `storage/qdrant`) |
| Keyword search | rank_bm25 (BM25Okapi) |
| Fusion / re-rank | Reciprocal Rank Fusion (k=60); `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| LLM | Provider-agnostic wrapper (Anthropic or OpenAI SDK), temperature 0, structured output |
| Agents | LangGraph (+ langchain-core) |
| Models / config | Pydantic v2, PyYAML, python-dotenv, python-dateutil |
| CLI | Typer + Rich |
| API | FastAPI + Uvicorn |
| Tests | pytest (backend); Vitest + React Testing Library (frontend) |
| Frontend | Vite + React 18 + TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, React Router, lucide-react, Recharts, react-markdown, openapi-typescript |
| Bonus infra | Docker, docker-compose, GitHub Actions |

---

## 3. Repository layout

```
rfp-intel/
  CLAUDE.md
  main.py                     # Typer CLI: parse|index|search|extract|ask|eval|run|compare, default --bid
  requirements.txt            # pinned
  .env.example                # LLM_PROVIDER, LLM_API_KEY, EXTRACT_MODEL, REASON_MODEL
  .gitignore                  # .env, .venv, storage/, outputs/, logs/, __pycache__, node_modules, frontend/dist
  config/
    settings.yaml
    fields.yaml               # the 20 fields
    synonyms.yaml             # query expansion
    ingestion.yaml            # HTML labels, filler regexes, classifier patterns
  rfp_intel/
    __init__.py
    config.py                 # Settings loader (YAML + env)
    schemas.py                # ALL Pydantic models
    logging_utils.py          # StepLog JSONL writer, @log_step decorator
    llm.py                    # structured(), text(); retries; usage
    ingestion/
      loader.py  pdf_parser.py  html_parser.py  cleaner.py  classifier.py
    search/
      chunker.py  embedder.py  vector_store.py  bm25_store.py
      query.py  fusion.py  reranker.py  engine.py
    agents/
      state.py
      baseline.py             # Phase 5 single-pass extractor
      prompts/                # orchestrator.md extract.md reconcile.md validate.md answer.md summary.md
      nodes/                  # ingest.py orchestrate.py retrieve.py extract.py reconcile.py validate.py report.py answer.py
      graph.py                # build_extraction_graph(), build_qa_graph()
    services.py               # functions shared by CLI and API
    api/server.py             # FastAPI app
  frontend/                   # React app (Phase 7b)
  eval/
    retrieval_questions.jsonl
    gold/Bid1.json  gold/Bid2.json
    loaders.py  run_retrieval_eval.py  run_extraction_eval.py
    results/                  # generated reports
    qa_log.md
  tests/
    fixtures/mini_bid/        # tiny generated PDF + HTML
  data/Bid1  data/Bid2        # inputs
  outputs/  logs/  storage/   # generated
  docs/
    BUILD_PLAN.md  ASSUMPTIONS.md  architecture.png
  README.md
```

---

## 4. Configuration

### config/settings.yaml

```yaml
paths: {data_dir: data, storage_dir: storage, outputs_dir: outputs, logs_dir: logs}
ingestion: {min_page_chars: 20, ocr: {enabled: false, lang: eng}}
chunking: {max_tokens: 400, overlap_tokens: 60, min_tokens: 40}
embedding: {provider: local, model: BAAI/bge-small-en-v1.5, batch_size: 32,
            query_prefix: "Represent this sentence for searching relevant passages: "}
vector_store: {kind: qdrant_local, collection: rfp_chunks}
retrieval: {candidates_per_retriever: 30, rrf_k: 60, rerank_top_n: 20, final_top_k: 5,
            use_bm25: true, use_reranker: true}
retrieval_configs:            # named configs for eval and --config
  vector:        {use_bm25: false, use_reranker: false}
  hybrid:        {use_bm25: true,  use_reranker: false}
  hybrid_rerank: {use_bm25: true,  use_reranker: true}
reranker: {model: cross-encoder/ms-marco-MiniLM-L-6-v2}
llm: {temperature: 0, timeout_s: 60, max_json_retries: 1, max_tokens: 2000}
agents: {max_field_retries: 2, parallel_extractors: true, min_confidence: 0.5,
         evidence_top_k: 6, widened_top_k: 10}
precedence: [addendum, rfp, bid_page, specs, affidavit, form, other]
eval: {recall_k: [1, 3, 5, 10]}
cost: {usd_per_1k_input: {}, usd_per_1k_output: {}}   # per model name
cache: {enabled: false, similarity_threshold: 0.95}
api: {cors_origins: ["http://localhost:5173"]}
```

### .env.example

```
LLM_PROVIDER=anthropic          # or openai
LLM_API_KEY=
EXTRACT_MODEL=                  # fast, low-cost model
REASON_MODEL=                   # stronger model for validation and Q&A
```

### config/fields.yaml

Exact field names (keys in the output JSON). `group` decides which extractor
handles the field; `doc_types` optionally widens/narrows retrieval.

```yaml
- name: Bid Number
  group: commercial_legal
  description: Official solicitation / RFP identifier(s). Return every official ID found (RFP number, sourcing or portal number), primary first.
  search_hints: [solicitation number, RFP number, bid number, reference number, PORFP number, sourcing number]
- name: Title
  group: commercial_legal
  description: Full title of the bid or RFP.
  search_hints: [title, request for proposal, solicitation title]
- name: Due Date
  group: dates_logistics
  description: Final submission deadline with date, time and time zone, reflecting any addendum changes. Give the value as written and note the original date if changed.
  search_hints: [due date, closing date, submission deadline, solicitation due, proposal due, extend the due date]
- name: Bid Submission Type
  group: dates_logistics
  description: How bids are submitted (electronic portal, sealed paper, email) and any prohibited methods.
  search_hints: [submit, submission, electronic, portal, sealed envelope, hard copy, email submissions, fax]
- name: Term of Bid
  group: dates_logistics
  description: Contract duration and renewal options, or proposal validity period if no contract term is stated.
  search_hints: [term, length of contract, renewal, initial term, extension, valid for days]
- name: Pre Bid Meeting
  group: dates_logistics
  description: Date, time, location/format and whether attendance is mandatory, or "None".
  search_hints: [pre-proposal meeting, pre-bid conference, prebid, mandatory, attendance]
- name: Installation
  group: dates_logistics
  description: Whether installation, deployment, imaging, asset tagging or setup services are required, and what they include.
  search_hints: [installation, deployment, white glove, imaging, asset tag, etching, setup, autopilot]
- name: Bid Bond Requirement
  group: commercial_legal
  description: Required bid bond or bid security amount/percentage. If none is stated, status not_found.
  search_hints: [bid bond, bid security, surety, bond, guarantee]
- name: Delivery Date
  group: dates_logistics
  description: Required delivery date or delivery window after award, and delivery location if stated.
  search_hints: [delivery, deliver within, days of award, ship, shipping]
- name: Payment Terms
  group: commercial_legal
  description: Payment terms (e.g. Net 30) and invoicing conditions.
  search_hints: [payment terms, net 30, invoice, invoicing, payment]
- name: Any Additional Documentation Required
  group: commercial_legal
  description: Affidavits, forms, certificates, insurance, W-9, references, addendum acknowledgments and other documents bidders must submit.
  search_hints: [affidavit, form, certificate, W-9, W9, insurance, references, attach, acknowledge addendum, 1295, MWBE]
  doc_types: [rfp, addendum, affidavit, form, bid_page]
- name: MFG for Registration
  group: commercial_legal
  description: Manufacturer(s) the bidder must be registered with or authorized by (authorized reseller, letter of authorization, factory certifications).
  search_hints: [authorized reseller, manufacturer, letter of authorization, factory-authorized, OEM]
- name: Contract or Cooperative to use
  group: commercial_legal
  description: Referenced cooperative, master contract or state contract vehicle, if any.
  search_hints: [master contract, cooperative, interlocal, rider, piggyback, contract number]
- name: Model_no
  group: product_specs
  description: Model number(s) of requested products. not_found if the bid only lists generic specifications.
  search_hints: [model, latitude, product name, model number]
- name: Part_no
  group: product_specs
  description: Part / SKU numbers of requested products.
  search_hints: [SKU, part number, SI#, item number]
- name: Product
  group: product_specs
  description: Product name(s) with quantities.
  search_hints: [quantity, qty, product, item, target quantity, each]
- name: contact_info
  group: commercial_legal
  description: Procurement contact(s): name, email, phone. Include all distinct contacts, primary first.
  search_hints: [contact, buyer, procurement officer, email, phone, point of contact]
- name: company_name
  group: commercial_legal
  description: Issuing organization / agency name.
  search_hints: [issuing organization, district, agency, office, purchasing department]
- name: Bid Summary
  group: report
  description: 3-6 sentence summary generated from the validated fields.
  search_hints: []
- name: Product Specification
  group: product_specs
  description: Key technical specifications (CPU, RAM, storage, display, wireless, OS, warranty). For multi-tier bids, summarize per tier.
  search_hints: [specifications, minimum requirements, processor, memory, storage, display, warranty]
```

### config/synonyms.yaml (starter; generic, not bid-specific)

```yaml
deadline: [due date, closing date, submission deadline, proposals due]
due date: [closing date, deadline, submission deadline]
bond: [bid bond, bid security, surety]
warranty: [OEM warranty, limited hardware warranty, extended warranty]
contact: [buyer, procurement officer, point of contact, email, phone]
delivery: [deliver, shipping, ship date, days of award]
affidavit: [certification, form, attestation]
meeting: [pre-bid conference, pre-proposal meeting, prebid]
specs: [specifications, minimum requirements, configuration]
```

### config/ingestion.yaml

```yaml
html_labels: [Solicitation Number, Title, Reference Number, Issuing Organization,
  Solicitation Type, Location, Publication, Closing Date, Prebid Conference,
  Questions Due By, Contact Information, Description, Purchase Type, Piggyback Contract]
html_drop_selectors: [script, style, nav, header, footer, noscript, form]
filler_patterns:
  - '^Type$'
  - '^Provide your answer below$'
  - '^Circle one from the response values below:?$'
  - '^[-.…_]{10,}$'
  - '^No - I Do not agree \. The District shall consider a NO response.*'
  - '^Page \d+ of \d+$'
classifier:
  addendum: 'addendum\s*(no\.?|#|number)?\s*(\d+)'
  affidavit: 'affidavit'
  specs: '(\bspecs?\b|specification sheet|\bSKU\b)'
  rfp: '(request for proposal|purchase order request|\bRFP\b|\bPORFP\b|invitation (to|for) bid|\bIFB\b)'
  form: '(\bform\b|\bW-?9\b|questionnaire)'
```

---

## 5. Data models (`rfp_intel/schemas.py`)

All modules exchange these models. No raw dicts across module boundaries.

```python
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field

class DocType(str, Enum):
    BID_PAGE = "bid_page"; RFP = "rfp"; ADDENDUM = "addendum"
    SPECS = "specs"; AFFIDAVIT = "affidavit"; FORM = "form"; OTHER = "other"

class Page(BaseModel):
    number: int                          # 1-based; HTML = 1
    text: str
    tables: list[list[list[str]]] = []   # rows x cells
    is_empty: bool = False

class Document(BaseModel):
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
    file: str
    page: int
    chunk_id: str
    quote: str                           # exact supporting span, <= 300 chars

class SearchFilters(BaseModel):
    bid_id: str | None = None
    bid_ids: list[str] | None = None
    doc_types: list[DocType] | None = None
    addendum_number: int | None = None

class SearchHit(BaseModel):
    chunk: Chunk
    score: float
    ranks: dict[str, int] = {}           # {"vector": 3, "bm25": 1, "rerank": 1}

# ---- agent-to-agent messages ----
class Task(BaseModel):
    group: str                           # dates_logistics | commercial_legal | product_specs | qa
    fields: list[str] = []
    queries: list[str] = []
    filters: SearchFilters = SearchFilters()

class Plan(BaseModel):
    mode: Literal["extract", "qa"]
    question_type: Literal["extract", "single", "comparison", "changes"] = "extract"
    tasks: list[Task]

class EvidenceRequest(BaseModel):
    field_or_question: str
    queries: list[str]
    filters: SearchFilters
    top_k: int = 6

class EvidenceBundle(BaseModel):
    request: EvidenceRequest
    hits: list[SearchHit]
    expanded_queries: list[str]

class FieldExtraction(BaseModel):        # LLM output per field
    field: str
    value: str | list[str] | None
    evidence_ids: list[str]              # ["E2", "E5"] -> mapped to Citation in code
    quotes: list[str]
    status: Literal["ok", "not_found"]
    notes: str = ""

class FieldValue(BaseModel):
    value: str | list[str] | None
    sources: list[Citation] = []
    confidence: float = Field(ge=0, le=1)
    notes: str = ""
    status: Literal["ok", "not_found", "failed"] = "ok"

class AddendumChange(BaseModel):
    field: str
    kind: Literal["override", "clarification", "addition"]
    old_value: str | None
    new_value: str
    addendum_number: int
    source: Citation

class ValidationResult(BaseModel):
    field: str
    passed: bool
    checks: dict[str, bool]              # has_citation, quote_in_chunk, supported, format_ok, consistent
    reason: str = ""

class BidRecord(BaseModel):              # deliverable JSON (outputs/<bid>.json)
    bid_id: str
    fields: dict[str, FieldValue]        # exactly the 20 field names
    addendum_changes: list[AddendumChange] = []
    validation: dict[str, int]           # {"passed", "failed", "not_found"}
    generated_at: str
    run_id: str

class Answer(BaseModel):
    question: str
    answer: str
    citations: list[Citation]
    confidence: float
    bids: list[str]

class StepLog(BaseModel):                # one line of logs/trace_<run_id>.jsonl
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

class RunStatus(BaseModel):
    run_id: str
    bid_id: str
    status: Literal["running", "done", "failed"]
    steps: list[StepLog]
```

`BidRecord` must validate that `fields` contains exactly the 20 names in
`config/fields.yaml` (model validator).

---

## 6. Module specs

### 6.1 Ingestion (`rfp_intel/ingestion/`)

No LLM calls. One bad file never fails the run.

| Function | Signature | Behaviour |
|---|---|---|
| `load_bid_folder` | `(folder: Path) -> list[Document]` | bid_id = folder name. Per file: sha1, dispatch by extension (.pdf, .html, .htm; others: warning + skip). try/except per file -> warning. Duplicate hash indexed once. |
| `parse_pdf` | `(path) -> list[Page]` | PyMuPDF `page.get_text("text", sort=True)`; pdfplumber `extract_tables()` (keep >=2 rows and >=2 cols). < `min_page_chars` -> `is_empty=True` + warning; OCR at 300 DPI if enabled. Encrypted: try empty password, else warning. |
| `parse_html` | `(path) -> tuple[list[Page], dict[str,str]]` | Read with `errors="replace"`. Drop `html_drop_selectors`. For each label in `html_labels`, take the next non-empty sibling/following text. Also collect `dt/dd` and `th/td` pairs. Output one Page of `Label: Value` lines + the fields dict. |
| `clean_pages` | `(pages) -> list[Page]` | Remove lines repeated on >= 50% of pages; remove `filler_patterns`; join hyphenated line breaks; collapse whitespace; keep paragraph breaks. |
| `classify` | `(file_name, first_page_text, is_html) -> tuple[DocType, int|None]` | HTML -> bid_page; then addendum (+number from name or text), affidavit, specs, rfp, form, other using `classifier` patterns. |
| `extract_doc_date` | `(text) -> str|None` | First date near "Issue Date", "Date:" or in first 500 chars, dateutil, ISO. |

CLI: `python main.py parse --bid data/Bid1` writes `outputs/parsed/<bid>/<file>.json`
and prints `file | doc_type | addendum | pages | tables | warnings`.

### 6.2 Search engine (`rfp_intel/search/`)

`SearchEngine` is the only entry point:
`index_bid(folder, force=False) -> IndexStats` and
`search(query, filters=None, top_k=None, config=None) -> list[SearchHit]`.

**Chunking**
1. `detect_sections(text)`: heading = line with >= 70% uppercase letters
   (3-80 chars), or `^\d+(\.\d+)*\s+[A-Z]`, or ending in "Requirements".
2. Special units kept whole (split only if > max_tokens):
   `qa` (addendum: split on `^\d+\.\s`, question + "Answer:" together),
   `spec_list` (a "...Requirements" heading + its bullets),
   `table` (rows as `a | b | c`; repeat header row when split),
   `record` (HTML page, one chunk).
3. Remaining text: pack paragraphs to `max_tokens`, then sentences;
   `overlap_tokens` carried over. Drop chunks < `min_tokens` unless `qa`/`record`.
4. `header = f"{bid_id} | {doc_type}{' ' + str(n) if n else ''} | {file_name} | p{page} | {section or ''}"`.
   Embed and BM25-index `header + "\n" + text`.

**Indexing**
- `Embedder`: normalized embeddings; `query_prefix` on queries only.
- `VectorStore`: Qdrant embedded at `storage/qdrant`; payload = full Chunk;
  payload indexes on bid_id, doc_type, addendum_number; `upsert`,
  `delete_by_bid`, `search(vector, filters, limit)`.
- `BM25Store`: one BM25Okapi per bid at `storage/bm25/<bid>.pkl` with chunk
  ids. Tokenizer: lowercase, regex `[a-z0-9]+(?:[-#][a-z0-9]+)*` so
  "JA-207652", "WD22TB4", "SI#" stay whole.
- Manifest `storage/manifest.json`: bid_id -> file hashes. Unchanged bid ->
  skip; changed -> delete_by_bid + re-add. Second run adds 0 chunks.

**Search**
1. `expand_query(q)` using synonyms.yaml (whole-phrase match).
2. `parse_filters(q, known_bids)`: known bid ids -> bid_id; "addendum N" ->
   addendum filter; doc-type words ("specs", "spec sheet", "addendum",
   "affidavit", "RFP", "portal", "bid page") -> doc_types. Explicit filters win.
3. Vector and BM25 each return `candidates_per_retriever`.
4. `rrf(lists, k)`: score = sum 1/(k + rank).
5. Cross-encoder re-ranks top `rerank_top_n` on (query, header+text).
6. Return `final_top_k` hits with `ranks` filled.

CLI: `python main.py index --bid data/Bid1 [--force]`;
`python main.py search "<q>" --bid Bid1 --type addendum --top-k 5 --config hybrid_rerank`
prints rank, score, file, page, doc_type, first 200 chars.

### 6.3 LLM wrapper (`rfp_intel/llm.py`)

- `structured(prompt: str, model_cls: type[T], model: str, system: str|None) -> tuple[T, Usage]`
  using the provider's tool-calling / JSON-schema mode; temperature 0;
  timeout from config; on invalid JSON retry once with the validation error
  appended; raise `LLMError` after that.
- `text(prompt, model, system) -> tuple[str, Usage]`.
- `Usage` = tokens_in, tokens_out, cost_usd (from `cost` table, 0 if unknown).
- Provider chosen by `LLM_PROVIDER`; models by `EXTRACT_MODEL`, `REASON_MODEL`.
- Optional dev cache: `storage/llm_cache/` keyed by sha256(prompt+model) when `LLM_DEV_CACHE=1`.

### 6.4 Multi-agent graph (`rfp_intel/agents/`)

**State**

```python
class GraphState(TypedDict, total=False):
    run_id: str
    mode: Literal["extract", "qa"]
    bid_ids: list[str]
    question: str | None
    plan: Plan
    evidence: Annotated[dict[str, EvidenceBundle], merge_dicts]
    drafts: Annotated[dict[str, FieldValue], merge_dicts]
    addendum_changes: list[AddendumChange]
    validation: dict[str, ValidationResult]
    retries: dict[str, int]
    to_retry: list[str]
    answer: Answer | None
    record: BidRecord | None
    errors: Annotated[list[str], operator.add]
    usage: Annotated[list[StepLog], operator.add]
```

**Extraction graph**

```
START -> ingest -> orchestrate
      -> per field group in parallel (Send): retrieve -> extract_<group>
      -> reconcile -> validate
           --(to_retry and retries < max_field_retries)--> retrieve (widened, failed fields) -> extract -> reconcile
           --(else)--> report -> END
```

**QA graph**

```
START -> orchestrate -> retrieve -> answer -> validate_answer
           --(unsupported and retry < 1)--> retrieve (widened)
           --(else)--> END
```

| Node (agent) | Logic |
|---|---|
| ingest (Ingestion agent) | Calls ingestion + `engine.index_bid()` per bid (incremental). Logs docs, chunks, warnings. |
| orchestrate (Orchestrator) | Extract: Plan with 3 Tasks from fields.yaml (no LLM). QA: one LLM call -> question_type (single, comparison, changes), bids, doc_types, sub-queries. |
| retrieve (Retrieval agent) | ONLY caller of `engine.search`. Per field: EvidenceRequest from description + search_hints (+ doc_types from fields.yaml), bid filter, top_k `evidence_top_k`. Widened retry: more synonyms, `widened_top_k`, doc-type filter dropped. Reconcile requests: doc_types=[addendum]. Empty -> bundle with no hits (not an error). |
| extract_<group> (Extraction agents) | One LLM call per group (EXTRACT_MODEL) with all its fields + numbered evidence `[E1] (file, p.N) header\ntext`. Returns list[FieldExtraction]. Code maps evidence ids -> Citation (quote = model quote if found in chunk, else first 300 chars); unknown ids dropped. No hits -> not_found without LLM. |
| reconcile (Addendum Reconciler) | Per field, addendum evidence; LLM (REASON_MODEL) labels override / clarification / addition / none. Apply `precedence` (latest addendum wins). Old value + source -> notes; AddendumChange appended. |
| validate (Validator/Critic) | Rules: has_citation; quote_in_chunk (whitespace-normalized substring); format_ok (dates parse with dateutil; emails/phones regex). Consistency: due date after issue/publication date; pre-bid before due date; Model_no/Part_no appear in Product or spec evidence; normalize time zones before comparing (EDT vs CST/CDT). Then one LLM support check per group. Failures -> to_retry while retries < max; then status failed, low confidence. |
| report (Report agent) | Bid Summary (3-6 sentences) from validated fields only (REASON_MODEL); BidRecord; counts; writes `outputs/<bid>.json` and `outputs/<bid>_summary.md`. |
| answer (Q&A agent) | Answers only from evidence with citations or "Not found in documents". Comparison: evidence per bid, side-by-side. Changes: addendum evidence + reconciler change log (from saved BidRecord if present). |

**Confidence** = 0.4*normalized rerank score of best cited hit + 0.3*(validator
passed) + 0.2*(>= 2 agreeing sources) + 0.1*(no conflict). not_found with >= 6
relevant passages checked -> 0.8, else 0.5.

**Prompts** (`agents/prompts/*.md`) must state: role; "Use ONLY the evidence
passages"; "If the evidence does not contain the answer, return status
not_found and value null"; "Quote the exact supporting text"; cite evidence
ids; field descriptions; output schema.

**@log_step** wraps every node: writes a StepLog (input/output summaries,
tool calls incl. search queries and LLM calls, tokens, cost, latency, error),
catches exceptions into `errors`, returns a safe partial state.

### 6.5 Services, CLI and API

`rfp_intel/services.py` holds the functions both CLI and API call:
`index_bid`, `search`, `extract(bid_id) -> BidRecord`, `ask(question, bid_ids)
-> Answer`, `list_bids`, `get_record`, `run_status`.

CLI (Typer, `main.py`):

| Command | Does |
|---|---|
| `python main.py --bid ./data/Bid1` | Default: index + extract + print a results table (the brief's one command) |
| `python main.py --bid ./data/Bid1 --ask "..."` | Index if needed, answer one question |
| `parse`, `index`, `search`, `extract`, `ask`, `run` | As named |
| `eval retrieval`, `eval extraction` | Write eval/results/*.md and .json |
| `compare Bid1 Bid2` | Bonus (Phase 9) |

API (FastAPI, `rfp_intel/api/server.py`), engine loaded once (lifespan),
CORS from `api.cors_origins`, error JSON `{error, run_id}`:

| Method | Path | Request | Response |
|---|---|---|---|
| GET | /health | - | status, indexed bids |
| GET | /bids | - | list: bid_id, title, agency, due date, doc count, indexed, last run, validation counts |
| POST | /bids | multipart: bid_id, files[] | saves to data/<bid_id>, indexes, returns IndexStats |
| POST | /index | {folder, force} | IndexStats |
| GET | /bids/{bid_id} | - | documents (type, pages, warnings), stats |
| GET | /bids/{bid_id}/record | - | latest BidRecord (404 if none) |
| POST | /extract | {bid_id} | {run_id}; runs in background |
| GET | /runs/{run_id} | - | RunStatus |
| GET | /search | q, bid_id, doc_type, addendum, top_k, config | {hits: [...]} |
| POST | /ask | {question, bid_ids?} | Answer |
| GET | /chunks/{chunk_id} | - | Chunk |
| GET | /files/{bid_id}/{file_name} | - | original file (reject path traversal) |
| GET | /eval/retrieval, /eval/extraction | - | latest results JSON |

Uploads: only .pdf/.html/.htm; bid_id matches `^[A-Za-z0-9_-]{1,64}$`.

### 6.6 React web app (`frontend/`)

Stack: Vite + React 18 + TypeScript (strict), Tailwind CSS, shadcn/ui,
TanStack Query, React Router, lucide-react, Recharts, react-markdown.
Types: `npx openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.ts`;
typed client `src/api/client.ts` (base URL `VITE_API_URL`, default
`http://localhost:8000`).

Layout: left sidebar (Bids, Search, Ask, Evaluation); top bar with bid
switcher and light/dark toggle; one neutral palette + one accent.

| Screen | Route | Content |
|---|---|---|
| Bids | `/` | Bid cards: title, agency, due date (+ "changed by Addendum N" if an override exists), doc count, status, validation counts. "Add bid" dialog: drag-and-drop files + bid id -> POST /bids -> offer "Run extraction". |
| Bid detail | `/bids/:bidId` | Header (bid number, title, agency, due date). Tabs: Overview (Bid Summary + key-facts grid), Fields (20 rows: value, confidence bar, status badge, citation chips; row click -> right drawer with quotes, file, page, notes, "Open PDF at page" via `/files/...#page=N`), Addendum changes (old -> new timeline with citations), Documents (type, pages, warnings). "Run extraction" -> POST /extract, poll /runs/:id every 1.5 s, live step list, refresh record when done. |
| Search | `/search` | Query + filters (bid, doc type, addendum, config); result cards: file, page, type badge, score, ranks, highlighted terms; same citation drawer. |
| Ask | `/ask` | Chat with bid-scope multi-select; suggested questions (the 5 from the brief); answers with numbered citation markers opening the drawer; confidence badge. |
| Evaluation | `/eval` | Recharts bars: Recall@1/3/5/10 + MRR per config; per-question rank table; extraction accuracy per field. |
| Run trace | `/runs/:runId` | Step timeline: agent, node, latency, tokens, cost, retries, errors. |

UI rules: ok = green, not_found = gray, failed = red; confidence >= 0.8 green,
0.5-0.8 amber, < 0.5 red. Loading skeletons, empty states with a next action,
error states with retry, toasts. Keyboard accessible; works at >= 1024 px;
light and dark mode. No hard-coded bid data. Small typed components in
`src/components`, pages in `src/pages`, hooks in `src/hooks`.

---

## 7. Evaluation data

### 7.1 Retrieval questions (`eval/retrieval_questions.jsonl`)

One JSON per line: `{id, question, bid_id, expected_file, key_phrase}`.
A hit is relevant if its file == expected_file and its text contains
key_phrase (case- and whitespace-insensitive). Use the real file names in
`data/BidN`. Verify every key_phrase exists in its file (Phase 1).

| # | Question | Bid | Expected file | Key phrase |
|---|---|---|---|---|
| 1 | What is the new due date? | Bid1 | Addendum 2 | July 9, 2024 |
| 2 | When was the original solicitation due? | Bid1 | Main RFP | 27-JUN-2024 |
| 3 | Is there a pre-proposal meeting? | Bid1 | Main RFP | TEAMS video conference |
| 4 | How long is the contract term? | Bid1 | Main RFP | two (2) successive one (1) year |
| 5 | Must Form 1295 be submitted with the response? | Bid1 | Addendum 1 | submitted with the RFP response |
| 6 | Is etching required on monitors? | Bid1 | Addendum 1 | only require etching on Laptops |
| 7 | Tier 3 student Windows laptop memory requirement | Bid1 | Main RFP | 16GB RAM DDR5 |
| 8 | How many Tier 1 Chromebooks are estimated? | Bid1 | Main RFP | 50,000 |
| 9 | Who is the buyer? | Bid1 | Main RFP | JALZATE@dallasisd.org |
| 10 | Which cooperatives can ride the contract? | Bid1 | Main RFP | EPCNT |
| 11 | Can bids be emailed? | Bid1 | Main RFP | No fax or email submissions |
| 12 | What is the PORFP number? | Bid2 | PORFP | E20P4600040 |
| 13 | What is the closing time? | Bid2 | BidNet HTML | 06/10/2024 02:00 PM EDT |
| 14 | How soon must equipment be delivered? | Bid2 | PORFP | within 45 days of Award |
| 15 | Which affidavit concerns mercury? | Bid2 | PORFP | Mercury Affidavit |
| 16 | What processor does the Latitude 5550 have? | Bid2 | Dell Laptop Specs | Core Ultra 5 125U |
| 17 | Which master contract applies? | Bid2 | PORFP | 060B5400007 |
| 18 | How long is the extended warranty? | Bid2 | PORFP | 3 Years |
| 19 | WD22TB4 quantity | Bid2 | PORFP | WD22TB4 |
| 20 | How are invoices submitted? | Bid2 | PORFP | STOaccountspayable |

### 7.2 Gold answers (`eval/gold/Bid1.json`, `eval/gold/Bid2.json`)

Each field: `{value, must_contain: [...]}`. Scoring per field: exact /
partial / wrong. `must_contain` tokens are checked strictly; an LLM judge
compares meaning. not_found gold = `{value: null, status: "not_found"}`.

| Field | Bid1 (Dallas ISD) | Bid2 (Maryland Treasurer) |
|---|---|---|
| Bid Number | JA-207652 (RFP / sourcing 168884) | PORFP #E20P4600040; eMMA BPM044557 |
| Title | Student and Staff Computing Devices | Dell Laptops w/Extended Warranty |
| Due Date | 2024-07-09 2:00 PM Central (Addendum 2; originally 2024-06-27 2:00 PM) | 2024-06-10 2:00 PM EDT |
| Bid Submission Type | Electronic via Dallas ISD iSupplier portal (preferred) or one sealed hard copy to Procurement Services; no fax or email | Electronic only via eMMA; no email, fax, mail or hand delivery |
| Term of Bid | 3 years + two 1-year renewals, max 5 years | One-time fixed-price PO; proposals valid >= 90 days |
| Pre Bid Meeting | 2024-06-10 2:00 PM CST, Teams, not mandatory (highly encouraged) | None |
| Installation | Yes: white-glove deployment (asset tags, etching on laptops, asset reporting, delivery), Autopilot / Google enrollment | Not required (none stated) |
| Bid Bond Requirement | not_found (only general insurance/bond clause) | not_found |
| Delivery Date | No fixed date; quotes per project from Sept 2024; schedule set with Ed Tech | Within 45 days of award, to 80 Calvert St, Annapolis |
| Payment Terms | not_found | No Net terms stated; invoice within 10 days of delivery with PORFP number and proof of delivery |
| Additional Documentation | MWBE forms, W-9, Form 1295, signed Addenda 1-2, 3 references, factory-authorized repair certs (resellers), catalog/price list, insurance within 10 days of award | Contract Affidavit, Mercury Affidavit, warranty certificate on award, Dell LOA on request, ENERGY STAR products |
| MFG for Registration | None named; resellers need factory-authorized certifications; OEM warranties | Dell (authorized reseller) |
| Contract or Cooperative | EPCNT and CTPA rider options | Maryland Desktop, Laptop and Tablet 2015 Master Contract 060B5400007; Small Business Reserve |
| Model_no | not_found (vendor proposes) | Dell Latitude 5550; WD22TB4 |
| Part_no | not_found | SI# CC7802; WD22TB4; base SKU 210-BLYZ |
| Product | 10 device lines, e.g. 50,000 Tier 1 and 50,000 Tier 2 Chromebooks, 5,000 Tier 3 Windows laptops, 50,000 tablets, 10,000 each staff laptop tier, 5,000 each desktop tier and monitor type | 30 Dell Latitude 5550 laptops, 30 WD22TB4 docks, 3-year extended warranty |
| contact_info | Jasmine Alzate, JALZATE@dallasisd.org, (972) 925-4100; portal: ProcurementCS@dallasisd.org, 972-925-3700 | Tamaira Hawkins, 410-260-7533, thawkins@treasurer.state.md.us |
| company_name | Dallas Independent School District | Maryland State Treasurer's Office |
| Product Specification | Per tier, e.g. Tier 3: Intel i5 13th gen, 16 GB DDR5, 256 GB NVMe, Wi-Fi 6E, Windows 11 Pro, 3-yr warranty | Intel Core Ultra 5 125U, 16 GB DDR5, 256 GB NVMe, 15.6" FHD non-touch, Wi-Fi 6E, Windows 11 Pro, Copilot ready |
| Bid Summary | (not scored for exactness; judged for faithfulness) | (same) |

### 7.3 Q&A log (`eval/qa_log.md`)

At least 10 questions with cited answers, including the brief's five:
1. What is the submission deadline for Bid1 after all addendums?
2. Which affidavits are required for the Dell laptop bid?
3. Compare the warranty requirements of both bids.
4. Is a bid bond required, and if so, how much?
5. What changed in Addendum 2 compared to the original RFP?

---

## 8. Phases

Each phase: plan -> build -> run checks -> show output -> wait for approval ->
commit. Times are budgets for a one-day build (~11.5 h).

### Phase 0: Scaffold, config, schemas, LLM client (30 min)

Tasks
- Create the repository layout (Section 3); empty modules get docstrings.
- `requirements.txt` (pinned), `.gitignore`, `.env.example`.
- All config files from Section 4.
- `config.py`, `schemas.py` (Section 5, incl. BidRecord 20-field validator),
  `logging_utils.py` (StepLog writer + `@log_step`), `llm.py` (Section 6.3).
- `main.py` with Typer: placeholder commands and default `--bid` option.
- `tests/test_schemas.py`.

Acceptance checks
- `pip install -r requirements.txt` succeeds; `pytest -q` passes.
- `python main.py --help` lists all commands.
- `python -c "from rfp_intel.config import settings; print(settings.retrieval)"` works.
- A live `llm.structured()` call returns a filled Pydantic object and usage.
- `git status` does not show `.env`.

Commit: `phase-0-setup`

### Phase 1: Evaluation data (30 min)

Tasks
- `eval/retrieval_questions.jsonl` (Section 7.1) with real file names.
- `eval/gold/Bid1.json`, `eval/gold/Bid2.json` (Section 7.2).
- `eval/loaders.py` + `tests/test_eval_data.py`.
- Script that opens each expected_file (PyMuPDF / BeautifulSoup) and checks the
  key_phrase exists; print a pass/fail table; fix failing rows.

Acceptance checks
- Key-phrase table shows 20/20 found.
- Both gold files load and contain all 20 fields.

Commit: `phase-1-eval-data`

### Phase 2: Ingestion (1 h 30 min)

Tasks
- `rfp_intel/ingestion/*` per Section 6.1; patterns from `config/ingestion.yaml`.
- `parse` CLI command writing `outputs/parsed/<bid>/<file>.json` and a summary table.
- `tests/fixtures/mini_bid/` (generate a 2-page PDF with a header/footer, a
  table and an "Addendum No. 1" PDF; one HTML page with nav + label/value fields).
- Tests: test_pdf_parser, test_html_parser, test_cleaner, test_classifier,
  test_ingestion_failures (empty file, corrupt PDF, unknown extension).

Acceptance checks
- Parse table for Bid1 and Bid2: Addendum 1/2 -> addendum 1/2; Dell specs ->
  specs; both affidavits -> affidavit; both HTML -> bid_page.
- Bid1 RFP parsed text contains no "Provide your answer below"; Tier 1-3
  spec lists intact.
- Parsed HTML has Closing Date and Solicitation Number, no "Logout"/cookie text.
- `pytest -q` passes.

If short on time: keep the OCR flag and warning, skip OCR implementation.

Commit: `phase-2-ingestion`

### Phase 3: Search engine (2 h)

Tasks
- `rfp_intel/search/*` per Section 6.2 (chunker, embedder, Qdrant store,
  BM25 store + tokenizer, query expansion + filter parsing, RRF, re-ranker,
  SearchEngine, manifest-based incremental indexing).
- `index` and `search` CLI commands, `--config` selecting retrieval_configs.
- Tests: test_chunker, test_bm25_tokenizer, test_search (filters),
  test_incremental, test_query_expansion.

Acceptance checks
- `search "due date" --bid Bid1` -> Addendum 2 in top 3.
- `search "WD22TB4" --bid Bid2` -> PORFP first.
- `search "only search Bid2 specs processor"` -> only the specs file.
- Second `index` run adds 0 chunks.
- Three chunks printed in full: context header present, no half-cut Q&A pair.
- `pytest -q` passes.

If short on time: skip LLM query rewriting (synonym map only).

Commit: `phase-3-search`

### Phase 4: Retrieval evaluation (30 min)

Tasks
- `eval/run_retrieval_eval.py` + `python main.py eval retrieval`: for each
  question x config (vector, hybrid, hybrid_rerank), search with bid filter,
  top_k 10; compute Recall@1/3/5/10 and MRR.
- Write `eval/results/retrieval.md` (question set, summary table,
  per-question rank table) and `retrieval.json`.

Acceptance checks
- hybrid_rerank Recall@5 >= 0.85; hybrid >= vector on ID questions
  (12, 17, 19). For each miss, diagnose and fix generically (chunking,
  synonyms, filters), never per-question hacks; rerun.

Commit: `phase-4-retrieval-eval`

### Phase 5: Baseline extraction (1 h)

Tasks
- `agents/baseline.py`: per field build queries from description +
  search_hints, search (bid filter, top_k 6), numbered evidence, one LLM call
  per field group -> list[FieldExtraction]; map evidence ids to Citations;
  build BidRecord; save `outputs/<bid>.json`.
- `agents/prompts/extract.md` with the guardrail rules.
- `eval/run_extraction_eval.py` + `python main.py eval extraction`
  -> `eval/results/extraction.md` / `.json`.

Acceptance checks
- Both outputs validate as BidRecord with all 20 fields.
- Every non-null value has >= 1 citation; two citations opened show the quote on that page.
- Accuracy table recorded (baseline).

Commit: `phase-5-baseline`

### Phase 6a: Multi-agent extraction graph (1 h 15 min)

Tasks
- `agents/state.py`, `agents/nodes/*`, `agents/prompts/*`, `agents/graph.py`
  per Section 6.4 (retrieve is the only search caller; parallel extractors via
  Send; reconciler; validator rules + consistency + LLM check; retry loop max 2;
  report writes JSON + summary.md; every node wrapped in @log_step).
- Wire `python main.py --bid ./data/Bid1` and `extract` to the graph.
- Tests: test_validator_rules, test_error_handling (mocked LLM: timeout,
  bad JSON, empty retrieval -> run finishes with failed/not_found fields).

Acceptance checks
- Bid1 Due Date = 2024-07-09 2:00 PM Central, cites Addendum 2, notes the
  original June 27 date; addendum_changes lists it as an override.
- Graph accuracy >= baseline (show both).
- Trace: `retrieve` before every extraction and reconciliation; extractor
  steps overlap in time; at least one validator retry (or a test proving the path).
- Wrong API key -> run finishes with failed fields and a trace, no crash.
- Copy one full trace to `logs/example_trace.jsonl`.

Commit: `phase-6a-extraction-graph`

### Phase 6b: QA graph (45 min)

Tasks
- QA graph (Section 6.4): question classification, per-bid evidence for
  comparisons, change questions using addendum evidence + change log,
  validate_answer with one widened retry. `ask` CLI command.
- Write `eval/qa_log.md` (Section 7.3, >= 10 questions).

Acceptance checks
- Bond question says no bid bond is stated for either bid (no invented amount).
- Addendum 2 question names June 27 -> July 9, 2024 with both citations.
- Comparison answer cites both bids.

Commit: `phase-6b-qa-graph`

### Phase 7a: API (30 min)

Tasks
- `rfp_intel/services.py`; finish CLI; `api/server.py` per Section 6.5
  (background extraction runs with RunStatus; upload validation; path-traversal
  protection; CORS).
- Tests: test_api (TestClient, mocked LLM), test_cli (mini_bid end to end).

Acceptance checks
- curl every endpoint; responses match the documented shapes.
- `/docs` lists all endpoints; `/files/Bid1/..%2F.env` is rejected.
- `python main.py --bid ./data/Bid2` runs start to finish.
- `pytest -q` passes.

Commit: `phase-7a-api`

### Phase 7b: React web app (1 h 30 min)

Tasks
- `frontend/` per Section 6.6: Vite + React + TS strict, Tailwind, shadcn/ui
  (button, card, badge, tabs, table, sheet/drawer, dialog, input, select,
  skeleton, toast/sonner, tooltip), TanStack Query, React Router.
- Generate `src/api/schema.ts` with openapi-typescript; typed client + hooks.
- Screens in this order: Bids, Bid detail (Fields + citation drawer first),
  Ask, then Search, Evaluation, Run trace.
- Scripts: dev, build, lint, test. Vitest + RTL tests: Fields table renders
  status badges; citation drawer opens with quote and page.

Acceptance checks
- With the API running, `npm run dev` shows both bids on the Bids page.
- Bid1 -> Fields -> Due Date shows July 9, 2024 with an Addendum 2 citation;
  drawer shows the quote; "Open PDF at page" opens the right page.
- A suggested question in Ask returns an answer whose markers open the right sources.
- "Add bid" upload + "Run extraction" shows live steps, then the record.
- Dark mode and 1024 px width: nothing overlaps or is unreadable.
- `npm run lint && npm test && npm run build` pass.

If short on time: ship Bids, Bid detail and Ask; add the others after v1.0.

Commit: `phase-7b-web`

### Phase 8: Unseen bid, deliverables, README (1 h 15 min)

Tasks
- The user adds `data/Bid3/` (an unseen BidNet bid). Run
  `python main.py --bid ./data/Bid3`; list problems; fix generically only;
  rerun Bid1, Bid2, Bid3 and both evals (no regressions).
- Grep source for bid-specific strings (bid numbers, agency names, dates)
  and secrets; report results.
- README.md containing: overview; setup + dependencies (Python and Node);
  one-command run; how to run each mode (extract, ask, search, API, web app,
  eval); architecture diagram; design decisions with reasons (chunking size,
  overlap and special units; embedding model; retrieval strategy: hybrid,
  RRF, re-ranker, expansion, filters; agent framework and agent roles;
  prompts and guardrails; React stack); retrieval results table; extraction
  accuracy; sample Q&A link; trace link; assumptions; known limitations;
  repo tree.
- `docs/architecture.png` (or a Mermaid diagram in README) showing the
  search engine, agents and how they communicate.
- Confirm present: outputs/Bid1.json, Bid2.json (+ _summary.md),
  eval/results/retrieval.md, eval/results/extraction.md, eval/qa_log.md,
  logs/example_trace.jsonl, docs/ASSUMPTIONS.md.
- Walk Section 1 "Requirements checklist" row by row; print
  `id | requirement | proof | PASS/FAIL`; fix every FAIL.

Acceptance checks
- Every mandatory row PASS.
- Fresh clone + README steps only -> `python main.py --bid ./data/Bid1` works.

Commit: `phase-8-deliverables`, then `git tag v1.0`.

### Phase 9: Bonuses (only after v1.0; one commit each)

1. Cost and latency summary per run (sum StepLogs) at the end of each trace and in Run trace UI.
2. Bid comparison: `compare` node + `python main.py compare Bid1 Bid2` -> `outputs/comparison.md`; Compare page in the web app.
3. Go / no-go: `config/capabilities.yaml` (authorized manufacturers, delivery capacity, bonding, regions) + `go_no_go` node -> `outputs/<bid>_go_no_go.md` with met / not met / unknown and citations.
4. Docker + CI: `Dockerfile`, `docker-compose.yml` (API + built frontend), GitHub Actions running pytest, frontend lint/test/build and the retrieval eval.
5. Semantic cache: answers keyed by query embedding + bid filter; reuse when cosine >= `cache.similarity_threshold`.
6. OCR: implement the `ocr.enabled` path with pytesseract; test on an image-only PDF page.

After each: `pytest -q`, frontend checks, both evals; update README; tag v1.1, v1.2...

---

## 9. Definition of done

- `python main.py --bid ./data/Bid1` and `--bid ./data/Bid2` each run in one command.
- Both JSON files: all 20 fields with value, sources, confidence, notes; addendum_changes; validation counts.
- Bid1 Due Date reflects Addendum 2 with citation and original date in notes.
- Missing fields are null with "Not found in documents", never guessed.
- Hybrid search, filters, re-ranker, expansion and citations work from CLI, API and web app; re-indexing adds no duplicates.
- Retrieval report: question set + Recall@1/3/5/10 + MRR for vector, hybrid, hybrid_rerank.
- Trace: Retrieval agent before each extraction, parallel extractors, a validator retry; forced LLM failure does not crash.
- React app: Bids, Bid detail with citation drawer, Ask (plus Search, Evaluation, Run trace if time).
- Unseen bid runs with no code changes; no bid-specific strings or secrets in source.
- `pytest -q` and frontend lint/test/build pass.
- README, architecture diagram, Q&A log (>= 10), example trace, assumptions and limitations present; demo recorded.
