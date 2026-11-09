# Architecture

The search engine, the agents and how they communicate. GitHub renders the
Mermaid diagrams inline; `docs/BACKEND.md` walks through the same code in
more detail.

## 1. System overview

```mermaid
flowchart LR
    subgraph Ingestion
        F[PDF / HTML files in data/Bid] --> P[pdf_parser / html_parser]
        P --> C[cleaner + classifier]
        C --> D[Document: pages, tables, metadata]
    end
    subgraph Search engine
        D --> CH[chunker: qa / spec_list / table / record / text]
        CH --> E[bge-small embeddings] --> Q[(Qdrant: dense + payload filters)]
        CH --> B[(BM25 per bid)]
        Q --> RRF[RRF fusion]
        B --> RRF
        RRF --> RR[cross-encoder re-ranker] --> H[SearchHit: chunk + score + ranks]
    end
    subgraph Agents - LangGraph
        I[Ingestion agent] --> O[Orchestrator]
        O -- Send per field group --> R1[Retrieval agent]
        R1 --> X1[Extractor: dates/logistics]
        O -- Send --> R2[Retrieval agent] --> X2[Extractor: commercial/legal]
        O -- Send --> R3[Retrieval agent] --> X3[Extractor: product specs]
        X1 & X2 & X3 --> AR[Addendum Reconciler] --> V[Validator / Critic]
        V -- failed fields, max 2 retries, widened retrieval --> R1
        V --> RP[Report agent] --> J[outputs/bid.json + summary.md]
        O2[Orchestrator] --> R4[Retrieval agent] --> A[Q&A agent] --> VA[Validator] --> ANS[Answer + citations]
        J --> CMP[Comparison agent] --> CR[outputs/comparison.md]
        J --> GNG[Go/No-Go agent] --> GR[outputs/bid_go_no_go.md]
        CAP[(config/capabilities.yaml)] --> GNG
    end
    H -. only the Retrieval agent calls search .-> R1 & R4
    J & ANS & CR & GR --> API[FastAPI] --> UI[React app]
    API --> CLI[Typer CLI]
```

## 2. Extraction run (sequence)

```mermaid
sequenceDiagram
    participant U as CLI / API
    participant I as Ingestion
    participant O as Orchestrator
    participant R as Retrieval (x3, parallel)
    participant X as Extractors (x3, parallel)
    participant AR as Addendum Reconciler
    participant V as Validator
    participant RP as Report
    U->>I: bid folder
    I->>I: parse, clean, classify, index (incremental)
    I->>O: IndexStats
    O->>R: Plan: 3 Tasks (fields, queries, filters) via Send
    R->>R: hybrid search per field (+ addendum-only pass)
    R->>X: EvidenceBundle (numbered hits E1..En)
    X->>AR: FieldValue per field (value, citations with verified quotes)
    AR->>V: AddendumChange list, drafts updated (override / clarification / addition)
    V->>V: rules: citation, quote-in-chunk, format, consistency + LLM support check
    alt failed fields and retries left
        V->>R: to_retry (widened top_k)
        R->>X: new evidence
        X->>V: new drafts
    end
    V->>RP: ValidationResult per field
    RP->>U: BidRecord -> outputs/bid.json, summary.md, trace
```

## 3. Messages between agents

Every message is a Pydantic model in `rfp_intel/schemas.py`; nodes share one
typed `GraphState` (`rfp_intel/agents/state.py`) whose dict/list fields have
reducers so parallel branches merge instead of overwriting.

| From -> To | Model | Carries |
|---|---|---|
| Orchestrator -> Retrieval | `Plan` / `Task` | field group, field names, queries, `SearchFilters` |
| Retrieval -> Extractor / Q&A | `EvidenceRequest` -> `EvidenceBundle` | numbered `SearchHit`s (chunk, score, ranks) |
| LLM -> Extractor (code) | `FieldExtractions` | value, evidence ids, quotes, status, notes |
| Extractor -> Reconciler -> Validator | `FieldValue` | value, `Citation`s (file, page, chunk_id, quote), confidence, notes, status |
| Reconciler -> Report | `AddendumChange` | field, kind, old/new value, addendum number, source |
| Validator -> Report | `ValidationResult` | per-field checks and reason |
| Report -> API/CLI | `BidRecord` | the deliverable JSON |
| Q&A -> API/CLI | `Answer` | answer text, citations, confidence, bids, cached flag |
| Comparison -> API/CLI | `ComparisonReport` | rows (cell per bid, `differs`), narrative, highlights with citations |
| Go/No-Go -> API/CLI | `GoNoGoReport` | recommendation, criterion verdicts with citations |
| every node -> trace | `StepLog` | input/output summaries, tool calls, tokens, cost, latency, error |

Rules enforced by code, not prompts: only the Retrieval node calls the search
engine; every LLM call goes through `rfp_intel/llm.py` and returns a Pydantic
model; a value without a citation is marked failed; a quote that is not in its
cited chunk fails validation; missing information is `null` + `not_found` +
"Not found in documents".

## 4. Processes and storage

```mermaid
flowchart TB
    CLI[python main.py] --> SVC[rfp_intel/services.py]
    API[uvicorn rfp_intel.api.server] --> SVC
    UI[React app :5173] -- JSON --> API
    SVC --> ENG[SearchEngine]
    ENG --> QL[(storage/qdrant embedded)]
    ENG --> QS[(Qdrant server, QDRANT_URL)]
    ENG --> BM[(storage/bm25/*.pkl + manifest)]
    ENG --> AC[(answer cache collection)]
    SVC --> OUT[(outputs/*.json, *.md)]
    SVC --> LOG[(logs/trace_run.jsonl)]
```

Development uses the embedded store (one process at a time); `docker compose
up` runs a Qdrant server, the API and the built web app, and the CI workflow
(`.github/workflows/ci.yml`) runs the tests, the frontend checks and the
retrieval evaluation on every push.
