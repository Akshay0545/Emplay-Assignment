# Backend: what happens, where, and in which order

This document walks through the Python backend of the RFP Intelligence
Platform end to end: the files involved, the data that flows between them,
and the decisions taken at each step. Diagrams are Mermaid (rendered by
GitHub and most editors).

Contents

1. [Big picture](#1-big-picture)
2. [Module map](#2-module-map)
3. [Configuration and secrets](#3-configuration-and-secrets)
4. [Data models](#4-data-models)
5. [Ingestion: file -> Document](#5-ingestion-file---document)
6. [Chunking: Document -> Chunks](#6-chunking-document---chunks)
7. [Indexing: Chunks -> Qdrant + BM25 (incremental)](#7-indexing-chunks---qdrant--bm25-incremental)
8. [Search: query -> SearchHits](#8-search-query---searchhits)
9. [LLM wrapper](#9-llm-wrapper)
10. [Extraction graph (multi-agent)](#10-extraction-graph-multi-agent)
11. [Q&A graph](#11-qa-graph)
12. [Observability: the trace](#12-observability-the-trace)
13. [Services, CLI and API](#13-services-cli-and-api)
14. [Error handling](#14-error-handling)
15. [Evaluation](#15-evaluation)
16. [Storage layout on disk](#16-storage-layout-on-disk)
17. [Extending the backend](#17-extending-the-backend)

---

## 1. Big picture

```mermaid
flowchart LR
    subgraph Inputs
        D[data/&lt;Bid&gt;/*.pdf, *.html]
    end
    subgraph Backend
        ING[Ingestion<br/>rfp_intel/ingestion] --> CH[Chunker<br/>search/chunker.py]
        CH --> IDX[Index<br/>Qdrant + BM25]
        IDX --> SE[SearchEngine<br/>search/engine.py]
        SE --> AG[Agents - LangGraph<br/>rfp_intel/agents]
        AG --> LLM[llm.py<br/>OpenAI / Anthropic]
        AG --> OUT[outputs/&lt;Bid&gt;.json<br/>logs/trace_*.jsonl]
        SVC[services.py] --> SE
        SVC --> AG
    end
    subgraph Entry points
        CLI[main.py] --> SVC
        API[api/server.py] --> SVC
        UI[frontend] --> API
    end
    D --> ING
```

Three layers, one direction of dependency:

| layer | package | depends on | never does |
|---|---|---|---|
| ingestion | `rfp_intel/ingestion` | config, schemas | call an LLM, touch the index |
| search | `rfp_intel/search` | ingestion, config, schemas | call an LLM |
| agents | `rfp_intel/agents` | search (only through the Retrieval node), llm, schemas | read files directly (except saved records for change questions) |

`services.py` is the only module the CLI and the API call; they contain no logic of their own.

---

## 2. Module map

```
rfp_intel/
  config.py           Settings (config/*.yaml + .env), load_fields(), load_synonyms(), load_ingestion()
  schemas.py          every Pydantic model exchanged between modules and agents
  logging_utils.py    StepLog JSONL writer, @log_step decorator, record_tool_call / record_llm_call
  llm.py              structured() and text(): provider-agnostic LLM calls returning Pydantic models
  services.py         functions shared by CLI and API
  ingestion/
    loader.py         load_bid_folder(): per-file hashing, dispatch, classify, clean, title, date
    pdf_parser.py     parse_pdf(): PyMuPDF spans -> lines, pdfplumber tables, empty pages, OCR
    html_parser.py    parse_html(): label/value extraction from portal pages
    cleaner.py        clean_pages(): headers/footers, filler lines, hyphenation, whitespace
    classifier.py     classify(): doc type + addendum number; extract_doc_date()
  search/
    chunker.py        chunk_document(): qa / spec_list / table / record / text chunks
    embedder.py       Embedder: bge-small, normalized vectors, query prefix
    vector_store.py   VectorStore: Qdrant local, payload = Chunk, filters
    bm25_store.py     BM25Store + tokenize(): one corpus per bid, ID-preserving tokens
    query.py          expand_query() (synonyms), parse_filters() (bid / addendum / scoped doc type)
    fusion.py         rrf(): reciprocal rank fusion
    reranker.py       Reranker: cross-encoder
    engine.py         SearchEngine: index_bid() with manifest, search() pipeline
  agents/
    state.py          GraphState (TypedDict + reducers)
    runtime.py        shared engine (one Qdrant client per process), prompt loading
    rules.py          deterministic validator rules (citation, quote, format, consistency, time zones)
    baseline.py       single-pass extractor + the citation/quote helpers the graph reuses
    graph.py          build_extraction_graph(), build_qa_graph(), run_extraction(), run_question()
    prompts/*.md      orchestrator, extract, reconcile, validate, summary, answer
    nodes/            ingest, orchestrate, retrieve, extract, reconcile, validate, report, answer
  api/server.py       FastAPI app
```

---

## 3. Configuration and secrets

```mermaid
flowchart LR
    Y1[config/settings.yaml] --> S[config.py: Settings]
    Y2[config/fields.yaml<br/>20 fields: name, group,<br/>description, search_hints, doc_types] --> S
    Y3[config/synonyms.yaml] --> S
    Y4[config/ingestion.yaml<br/>HTML labels, filler regexes,<br/>classifier patterns] --> S
    E[.env<br/>LLM_PROVIDER, LLM_API_KEY,<br/>EXTRACT_MODEL, REASON_MODEL] -->|python-dotenv| S
    S --> ALL[every module imports<br/>`from rfp_intel.config import settings`]
```

- `settings` is a typed Pydantic object; `settings.retrieval_for("hybrid")`
  applies a named retrieval config; `settings.path("outputs_dir")` resolves
  paths against the project root.
- The API key is a `SecretStr` and is never logged or printed. Only `.env`
  holds secrets; `.gitignore` excludes it.
- `fields.yaml` drives everything field-related: the extractor groups, the
  retrieval queries (description + search_hints), the optional doc-type
  narrowing, and the `BidRecord` validator that insists on exactly those 20
  names. Adding a field is a config change.

---

## 4. Data models

All in `rfp_intel/schemas.py`. No raw dicts cross a module boundary.

```mermaid
classDiagram
    class Document { doc_id; bid_id; file_name; doc_type; addendum_number; doc_date; title; pages: Page[]; fields; warnings }
    class Page { number; text; tables; is_empty }
    class Chunk { chunk_id; bid_id; file_name; doc_type; addendum_number; page; section; kind; header; text; token_count }
    class SearchHit { chunk: Chunk; score; ranks }
    class Citation { file; page; chunk_id; quote }
    class FieldValue { value; sources: Citation[]; confidence; notes; status }
    class AddendumChange { field; kind; old_value; new_value; addendum_number; source: Citation }
    class BidRecord { bid_id; fields: dict~FieldValue~; addendum_changes; validation; generated_at; run_id }
    class Answer { question; answer; citations: Citation[]; confidence; bids }
    class StepLog { run_id; step; agent; node; started_at; input_summary; tool_calls; output_summary; tokens_in; tokens_out; cost_usd; latency_ms; error }
    Document *-- Page
    Chunk <.. Document : chunk_document()
    SearchHit *-- Chunk
    FieldValue *-- Citation
    BidRecord *-- FieldValue
    BidRecord *-- AddendumChange
    AddendumChange *-- Citation
    Answer *-- Citation
```

Agent-to-agent messages: `Plan`/`Task` (Orchestrator -> branches),
`EvidenceRequest`/`EvidenceBundle` (Retrieval -> Extractors / Reconciler /
Q&A), `FieldExtraction(s)` (extractor LLM output), `ReconcileDecision(s)`,
`SupportCheck(s)`, `QAPlan`, `AnswerDraft` (LLM outputs), `ValidationResult`.

---

## 5. Ingestion: file -> Document

Entry point: `load_bid_folder(folder)` in `ingestion/loader.py`. No LLM.

```mermaid
flowchart TD
    A[for each file in data/&lt;Bid&gt;] --> B{extension?}
    B -- other --> SKIP[log warning, skip]
    B -- .pdf / .html --> H[sha1 of bytes = doc_id]
    H --> DUP{hash seen before?}
    DUP -- yes --> SKIP2[log 'duplicate', skip]
    DUP -- no --> P{parse}
    P -- .pdf --> PDF[parse_pdf<br/>spans -> lines per page<br/>pdfplumber tables<br/>empty pages flagged<br/>OCR if enabled]
    P -- .html --> HTML[parse_html<br/>drop nav/script/footer<br/>label: value lines<br/>dt/dd, th/td pairs]
    PDF --> C[classify on RAW first page<br/>file name first, then text<br/>addendum N / affidavit / specs / rfp / form / other]
    HTML --> C2[doc_type = bid_page]
    C --> CL[clean_pages<br/>repeated headers/footers<br/>filler lines<br/>hyphenation, whitespace]
    C2 --> CL
    CL --> META[doc_date, title]
    META --> DOC[Document]
    P -- exception --> ERR[Document with no pages<br/>+ warning 'could not parse']
```

Details that matter:

- **PDF text is rebuilt from spans** (not `get_text()`): spans are grouped
  into rows by vertical position and joined with a space where there is a
  visible horizontal gap, so side-by-side columns ("part number | description")
  do not run together. A larger vertical gap becomes a paragraph break.
- **Classification happens before cleaning** because the cleaner removes
  repeated lines such as "ADDENDUM No. 1" that the classifier needs.
- **Cleaner rules:** a line is a header/footer if it repeats on >= 50% of
  pages (digits treated as wildcards, so "Page 3 of 40" == "Page 4 of 40"),
  is >= 4 chars and contains letters. Filler regexes come from
  `ingestion.yaml`. "procure-\nment" is joined; "JA-\n207652" is not.
- **Failures never stop the run:** unreadable, empty, encrypted or scanned
  files become Documents with warnings; the warnings reach the CLI table,
  `IndexStats.warnings` and `/bids/{id}`.

CLI: `python main.py parse --bid data/Bid1` writes `outputs/parsed/<bid>/<file>.json`.

---

## 6. Chunking: Document -> Chunks

`chunk_document(doc)` in `search/chunker.py`. The document is processed as
one stream of lines (page breaks are kept as markers), so units that cross a
page keep their text; `page` = the page a chunk starts on.

```mermaid
flowchart TD
    D[Document] --> T{doc_type == bid_page?}
    T -- yes --> REC[one 'record' chunk<br/>the whole label: value page]
    T -- no --> L[line stream over all non-empty pages]
    L --> Q{addendum and line matches '^N. '?}
    Q -- yes --> QA['qa' chunk: question + Answer:<br/>ends at question N+1 or a paragraph break<br/>page breaks do not end it]
    Q -- no --> HD{heading?}
    HD -- '...Requirements' --> SPEC['spec_list' chunk: heading + bullets<br/>skips running page headers<br/>continues across page breaks]
    HD -- uppercase / numbered --> SEC[new section label]
    HD -- no --> TXT[paragraph text]
    SEC --> PACK
    TXT --> PACK[pack paragraphs to max_tokens 400<br/>overlap 60 tokens into the next chunk<br/>split long paragraphs by sentence]
    D --> TB{page.tables}
    TB --> REAL{real table?<br/>>= 50% rows with 2+ cells}
    REAL -- yes --> TAB["'table' chunk: rows as 'a | b | c'<br/>header row repeated when split"]
    REAL -- no --> DROP[skip: layout grid, text already in page]
    QA & SPEC & PACK & TAB & REC --> K["keep: drop < 40 tokens unless qa/record,<br/>drop exact duplicates"]
    K --> HDR["header = 'Bid1 | addendum 2 | file | p1 | section'<br/>embed/BM25 text = header + newline + text"]
```

Why: RFP facts live in Q&A pairs, bullet spec lists, tables and the portal
record; cutting any of those in half is the most common way to lose an
answer. Free text gets 400-token windows with 60 tokens of overlap so a
clause and its qualifier stay together and a sentence on a cut is
retrievable from both sides. The header gives every chunk its own context
(bid, document type, file, page, section) for both retrievers and the LLM.

---

## 7. Indexing: Chunks -> Qdrant + BM25 (incremental)

`SearchEngine.index_bid(folder, force=False)` in `search/engine.py`.

```mermaid
sequenceDiagram
    participant C as caller: CLI / API / ingest node
    participant E as SearchEngine
    participant M as storage/manifest.json
    participant V as Qdrant storage/qdrant
    participant B as BM25Store storage/bm25/bid.pkl
    C->>E: index_bid(folder)
    E->>M: read {bid: {files: sha1..., config: hash}}
    alt unchanged and not force
        E-->>C: IndexStats(skipped=True, chunks_added=0)
    else changed / new / force
        E->>E: load_bid_folder -> chunk_document x N
        E->>E: Embedder.embed_documents(header+text)
        E->>V: delete_by_bid(bid) then upsert(points: id=uuid5(bid/chunk_id), payload=Chunk)
        E->>B: save(bid, chunks, tokens)
        E->>M: write manifest (atomic)
        E-->>C: IndexStats(documents, chunks_added, chunks_total, warnings, seconds)
    end
```

- The manifest hashes every supported file and the chunking/embedding
  settings, so a changed file or changed chunk size re-indexes that bid
  only, and a second run adds 0 chunks.
- Point ids are `uuid5(bid_id/chunk_id)`: the same file in two bids (a
  standard affidavit) does not overwrite itself.
- Qdrant runs in embedded local mode: one client per storage folder per
  process. `agents/runtime.get_engine()` hands out the single shared engine;
  the API opens it in its lifespan, the CLI closes it after each command.
- The vector size is cached in the manifest so skipping an unchanged bid does
  not load the embedding model (~1 s instead of ~15 s).

---

## 8. Search: query -> SearchHits

`SearchEngine.search(query, filters=None, top_k=None, config=None)`.

```mermaid
flowchart LR
    Q[query text] --> PF["parse_filters<br/>bid ids, 'addendum N',<br/>scoped doc types: 'only search ... specs',<br/>'in the RFP'"] --> MF["merge with explicit filters<br/>explicit wins"]
    PF --> CQ[cleaned query]
    CQ --> EX["expand_query<br/>synonyms.yaml, whole phrase,<br/>up to 4 variants"]
    EX --> V["vector search per variant<br/>bge-small query prefix<br/>Qdrant filter, 30 candidates"]
    EX --> K["BM25 per variant<br/>tokens keep IDs whole + parts<br/>30 candidates"]
    V & K --> RRF["RRF k=60 over all ranked lists"]
    RRF --> RR["cross-encoder re-rank top 20<br/>on query vs header+text"]
    RR --> OUT["final_top_k SearchHits<br/>ranks = {vector, bm25, rerank}"]
    MF --> V
    MF --> K
```

Named configs (`retrieval_configs` in settings.yaml): `vector` (dense only),
`hybrid` (dense + BM25 + RRF), `hybrid_rerank` (+ cross-encoder, the
default). The evaluation runs all three.

Notes:

- The BM25 tokenizer keeps `JA-207652`, `WD22TB4`, `SI#` whole and also
  emits their parts, so both "207652" and the full id match. BM25 results
  are "contains a query token, ranked by score" rather than "score > 0",
  because in small corpora a term present in half the chunks gets IDF 0.
- Multi-bid searches build one BM25 over the selected bids at query time so
  scores are comparable across bids.
- Doc-type words only become filters with scoping phrasing: "Which affidavit
  concerns mercury?" is a topic (the answer is in the PORFP), "only search
  Bid2 specs" is a scope.

CLI: `python main.py search "WD22TB4" --bid Bid2 --config hybrid_rerank`.
API: `GET /search?q=...&bid_id=...&doc_type=...&addendum=...&top_k=...&config=...`.

---

## 9. LLM wrapper

`rfp_intel/llm.py`: the only place that talks to a model.

```mermaid
flowchart LR
    A["structured(prompt, Model, model_name, system)"] --> S["strict JSON schema from the Pydantic model<br/>all properties required, no extras"]
    S --> P{"provider from LLM_PROVIDER"}
    P -- openai --> O["chat.completions<br/>response_format json_schema strict<br/>max_completion_tokens"]
    P -- anthropic --> AN["messages<br/>output_config.format json_schema"]
    O & AN --> V{"Model.model_validate_json"}
    V -- ok --> R["model instance + Usage: tokens, cost"]
    V -- invalid --> RETRY["retry once with the validation error appended"]
    RETRY --> V2{"valid?"}
    V2 -- no --> ERR[LLMError]
    O -- timeout / API error --> ERR
```

- Temperature is sent from config; if a model rejects it (reasoning models
  only accept their default) the call is retried without it and the model
  is remembered.
- Every call records tokens, latency and cost (prices per model in
  `settings.yaml` `cost`) into the current trace step via
  `record_llm_call`.
- `LLM_DEV_CACHE=1` caches identical calls on disk for development.
- Models with open-ended dicts cannot use strict mode; they fall back to a
  schema hint in the system prompt plus the same validation.

---

## 10. Extraction graph (multi-agent)

`build_extraction_graph()` in `agents/graph.py`. Run with
`python main.py --bid ./data/Bid1` (`run_extraction(bid_id)`).

```mermaid
flowchart TD
    START((START)) --> ING[ingest<br/>Ingestion agent<br/>index_bid per bid]
    ING --> ORC[orchestrate<br/>Orchestrator<br/>Plan: 3 Tasks from fields.yaml]
    ORC -- Send x3 in parallel --> FG1[field_group: dates_logistics]
    ORC -- Send --> FG2[field_group: commercial_legal]
    ORC -- Send --> FG3[field_group: product_specs]
    subgraph branch [field_group sub-graph, one per task]
        R[retrieve<br/>Retrieval agent<br/>per field: EvidenceRequest -> hybrid_rerank search<br/>+ addendum-only evidence] --> X[extract_&lt;group&gt;<br/>Extraction agent<br/>1 LLM call, EXTRACT_MODEL]
    end
    FG1 & FG2 & FG3 --> REC[reconcile<br/>Addendum Reconciler<br/>1 LLM call, REASON_MODEL<br/>override / clarification / addition]
    REC --> VAL[validate<br/>Validator / Critic<br/>rules + consistency + 1 support call per group]
    VAL -- to_retry and retries < 2 --> ORC2{Send widened branches<br/>for the groups owning failed fields}
    ORC2 --> FG1
    VAL -- else --> REP[report<br/>Report agent<br/>Bid Summary, BidRecord,<br/>outputs/&lt;bid&gt;.json + _summary.md]
    REP --> END((END))
```

State keys written by each node (reducers merge dicts/lists from parallel branches):

| node | reads | writes |
|---|---|---|
| ingest | bid_ids | errors |
| orchestrate | bid_ids, mode | plan |
| retrieve | task (Send payload), widened, to_retry | evidence[field], evidence[field::addendum] |
| extract_<group> | task, evidence | drafts[field] (FieldValue with citations) |
| reconcile | drafts, evidence[*::addendum], widened/to_retry | drafts (changed fields), addendum_changes |
| validate | drafts, evidence, retries | validation, drafts (confidence/status), retries, to_retry, widened |
| report | drafts, addendum_changes, run_id | record, drafts["Bid Summary"] |

What each agent does, in order:

1. **Retrieval** (the only node that calls `engine.search`): for every field
   it builds 2 queries from the field's description and search hints,
   filtered to the bid (and the field's `doc_types` if set), top 6, plus one
   addendum-only query (top 4) that feeds the reconciler. On a widened retry
   it uses up to 5 queries with synonym expansion, top 10, and drops the
   doc-type filter. Every search is recorded in the trace.
2. **Extractor** (one per group, in parallel): numbers the group's unique
   evidence `[E1] (file, p.N) header\ntext`, sends one structured prompt
   (`prompts/extract.md`) for all its fields, gets `FieldExtractions`, then
   in code: evidence ids -> `Citation`s (quote = the model's quote if it
   occurs in the chunk, whitespace-insensitively, else the chunk's first 300
   chars; unknown ids dropped), `not_found` for fields without evidence
   (no LLM call), `failed` for a value without any valid citation.
3. **Reconciler**: one batched prompt listing every field that has addendum
   evidence with its current value and notes; decisions are applied in
   addendum order (latest wins): override replaces the value and cites the
   addendum (old value + source go to notes), addition appends, clarification
   adds the citation and a note. Each change is an `AddendumChange`
   (one per field/kind/addendum; a rerun after a retry replaces its entry).
4. **Validator**: rules per field from `agents/rules.py`: has_citation,
   quote_in_chunk, format_ok (dates parse; contacts contain an email/phone),
   consistent (due date after publication, pre-bid before due date, model /
   part numbers appear in Product or evidence; time zones normalized to UTC,
   EDT vs CST). Then one LLM support check per group. Failed fields go to
   `to_retry` while retries < 2; after that they keep their value with
   status `failed` and confidence 0.2. Confidence = 0.4 x normalized
   re-rank score of the best cited hit + 0.3 x passed + 0.2 x (>= 2 sources)
   + 0.1 x no conflict; not_found = 0.8 when >= 6 passages were checked,
   else 0.5.
5. **Report**: Bid Summary (3-6 sentences) from validated fields only;
   `BidRecord` with exactly the 20 configured fields and validation counts;
   writes `outputs/<bid>.json` and `outputs/<bid>_summary.md`.

Why the `field_group` wrapper: LangGraph forbids three parallel branches
writing the same plain key in one step, so each branch runs the
`retrieve -> extract_<group>` sub-graph and returns only the reducer-merged
keys (evidence, drafts, usage, errors). The trace still shows a `retrieve`
step before every `extract_*` step and the three retrieves starting within
milliseconds of each other.

---

## 11. Q&A graph

`build_qa_graph()`; `python main.py ask "..." --bid Bid1` or `POST /ask`.

```mermaid
flowchart LR
    START((START)) --> O[orchestrate<br/>1 LLM call: QAPlan<br/>single / comparison / changes,<br/>bids, doc types, 2-4 queries]
    O --> R[retrieve<br/>per bid in scope: queries -> evidence 'qa::bid'<br/>change questions add queries from the saved change log]
    R --> A[answer<br/>Q&A agent, REASON_MODEL<br/>AnswerDraft -> citations with verified quotes<br/>or 'Not found in documents']
    A --> V[validate_answer<br/>quotes in chunks + 1 support check]
    V -- unsupported, first time --> R2[retrieve widened]
    R2 --> A
    V -- supported or retry used --> END((END))
```

- Comparison questions get evidence per bid and the prompt asks for a
  side-by-side answer citing each bid.
- Change questions ("what changed in Addendum 2") also load the bid's saved
  `addendum_changes` and derive extra queries from them (old value ->
  original passage, new value -> addendum passage), raising the evidence
  cap so both sides survive the merge.
- An answer that is still unsupported after the widened retry becomes
  "Not found in documents" rather than an unsupported claim.

---

## 12. Observability: the trace

`@log_step(agent, node)` wraps every node. For each node execution it writes
one `StepLog` line to `logs/trace_<run_id>.jsonl` and appends it to the
state's `usage` list:

```json
{"run_id": "...", "step": 10, "agent": "Validator", "node": "validate",
 "started_at": "2026-10-10T09:04:57.102+00:00",
 "input_summary": "run_id='...', bid_ids=list[1], evidence=dict[18], drafts=dict[19], ...",
 "tool_calls": [{"kind": "llm", "model": "gpt-5.4-mini", "tokens_in": 3900, "tokens_out": 210, "cost_usd": 0.0039, "latency_ms": 5200, "schema": "SupportChecks"},
                {"kind": "validation", "passed": 17, "failed": {"Bid Submission Type": "failed: supported"}, "to_retry": ["Bid Submission Type"]}],
 "output_summary": "validation=dict[19], drafts=dict[19], retries=dict[1], to_retry=list[1], widened=True",
 "tokens_in": 3900, "tokens_out": 210, "cost_usd": 0.0099, "latency_ms": 19034, "error": null}
```

Tool-call kinds: `search` (query, filters, hits), `index_bid`, `llm`
(model, tokens, cost, latency, schema), `validation`, `draft` and
`answer_check` (Q&A). A node that raises is caught: the error goes into the
StepLog and into `state["errors"]`, and the run continues with a safe
partial state. `GET /runs/{run_id}` and the web app's Run trace page read
this file; the CLI prints totals (steps, tokens, cost) after every run.

---

## 13. Services, CLI and API

```mermaid
sequenceDiagram
    participant UI as web app
    participant API as api/server.py
    participant S as services.py
    participant T as background thread
    participant G as agents/graph.py
    UI->>API: POST /extract {bid_id}
    API->>S: start_extraction(bid_id)
    S->>S: acquire single-run lock (409 if busy)
    S->>T: Thread(extract(bid_id, run_id))
    API-->>UI: 202 {run_id}
    T->>S: index_bid(data_dir/bid)
    T->>G: run_extraction(bid_id, run_id)
    G-->>T: state.record -> outputs/bid.json
    loop every 1.5 s
        UI->>API: GET /runs/{run_id}
        API->>S: run_status(run_id) = registry status + read_trace(run_id)
        API-->>UI: RunStatus {status, steps}
    end
    UI->>API: GET /bids/{bid_id}/record
    API-->>UI: BidRecord
```

`services.py` functions and who uses them:

| function | CLI | API |
|---|---|---|
| `index_bid(folder, force)` | `index`, before extract/ask | `POST /index`, `POST /bids` |
| `search(query, bid_id, doc_type, addendum, top_k, config)` | `search` | `GET /search` |
| `extract(bid_id)` -> BidRecord | `--bid`, `extract`, `run` | background worker |
| `start_extraction(bid_id)` -> run_id | - | `POST /extract` (202 / 409) |
| `run_status(run_id)` | - | `GET /runs/{id}` |
| `list_runs()`, `active_run()` | - | `GET /runs` (newest first; the running one is what a 409 refers to) |
| `ask(question, bid_ids)` -> Answer | `ask`, `--ask` | `POST /ask` |
| `list_bids()`, `get_bid_detail()`, `get_record()` | - | `GET /bids`, `/bids/{id}`, `/bids/{id}/record` |
| `get_chunk(chunk_id)` | - | `GET /chunks/{id}` |
| `save_upload(bid_id, files)` | - | `POST /bids` (only .pdf/.html/.htm, id `^[A-Za-z0-9_-]{1,64}$`) |
| `remove_bid(bid_id)` | `remove` | `DELETE /bids/{id}`: drops the bid from both stores and the manifest (`SearchEngine.remove_bid`), deletes `outputs/<id>*` and `outputs/parsed/<id>/`, then the data folder; 409 while an extraction of it runs |
| `safe_file_path(bid_id, name)` | - | `GET /files/{bid}/{file}` (rejects paths that escape the bid folder) |
| `eval_results(kind)` | - | `GET /eval/retrieval`, `/eval/extraction` |

API conventions: engine opened once in the lifespan; CORS from
`api.cors_origins`; every error is JSON `{"error": "...", "run_id": null}`
(400 validation, 404 missing, 409 busy, 422 bad body, 500 unexpected).

---

## 14. Error handling

| failure | where it is caught | effect |
|---|---|---|
| unreadable / empty / encrypted / scanned file | `loader.load_file` | Document with warning; run continues |
| unsupported extension, duplicate file | `loader.load_bid_folder` | skipped + logged |
| table extraction error | `pdf_parser._extract_tables` | warning; text still indexed |
| search error inside a node | `retrieve.run_request` | recorded as a search tool call with `error`; empty evidence |
| empty retrieval for a field | `extract` / `baseline.extract_group` | `not_found`, no LLM call |
| LLM timeout / API error / bad JSON after one retry | `llm.py` -> `LLMError` -> each node | extractor group -> every field `failed` with the error in notes; reconciler/validator -> drafts kept, error logged; summary -> failed; answer -> Not found |
| wrong API key | same path | run finishes with failed fields and a complete trace |
| node raises anything else | `@log_step` | error in StepLog + `state["errors"]`; safe partial state |
| validator failure | `validate` | retry (max 2) with widened retrieval, then status `failed`, confidence 0.2 |
| two extractions at once via API | `services.start_extraction` | 409 |
| path traversal on `/files` | `services.safe_file_path` | 400 |

Tests: `tests/test_error_handling.py`, `tests/test_ingestion_failures.py`, `tests/test_api.py`.

---

## 15. Evaluation

```mermaid
flowchart LR
    Q["eval/retrieval_questions.jsonl<br/>20 x {question, bid, expected_file, key_phrase}"] --> RE["eval/run_retrieval_eval.py<br/>search per config, bid filter, top 10<br/>relevant = expected file + key phrase"]
    RE --> R1["eval/results/retrieval.md / .json<br/>Recall@1/3/5/10, MRR per config"]
    G["eval/gold/Bid*.json<br/>value + must_contain per field"] --> EE["eval/run_extraction_eval.py<br/>tokens strict, LLM judge for free text<br/>gold 'None' == not_found"]
    O["outputs/Bid*.json"] --> EE
    EE --> R2["eval/results/extraction.md / .json<br/>exact / partial / wrong, accuracy"]
    QL["eval/run_qa_log.py<br/>12 questions through the QA graph"] --> R3["eval/qa_log.md"]
```

`eval/verify_eval_data.py` checks that every key phrase and gold token exists
in the real files before anything is scored.

---

## 16. Storage layout on disk

```
storage/
  manifest.json          {bid_id: {files: {name: sha1}, config: hash}, _dim: 384}
  qdrant/                Qdrant embedded collection 'rfp_chunks' (payload = Chunk)
  bm25/<bid>.pkl         chunks + tokens for the BM25 corpus of one bid
  llm_cache/             optional dev cache of LLM replies (LLM_DEV_CACHE=1)
outputs/
  parsed/<bid>/<file>.json   Documents from `parse`
  <bid>.json                 BidRecord (deliverable)
  <bid>_summary.md           summary paragraph + field table + change log
logs/
  trace_<run_id>.jsonl       one StepLog per node execution
```

---

## 17. Extending the backend

- **New field:** add an entry to `config/fields.yaml` (name, group,
  description, search_hints, optional doc_types) and a gold value to
  `eval/gold/*.json`. No code changes.
- **New document type:** add a pattern to `classifier` in
  `config/ingestion.yaml` and a member to `DocType`; precedence in
  `settings.yaml`.
- **New LLM provider:** implement the `Provider` protocol in `llm.py`
  (`complete(prompt, system, model, schema, schema_name) -> RawResponse`)
  and map it in `_provider()`.
- **New retrieval config:** add a name under `retrieval_configs` in
  `settings.yaml`; it is available to `--config`, `/search` and the eval.
- **New agent:** write a node function wrapped in `@log_step`, give its
  messages a Pydantic model in `schemas.py`, and wire it in `graph.py`.
  Only the Retrieval node may call `engine.search`.
