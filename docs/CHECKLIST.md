# Requirements checklist (BUILD_PLAN Section 1)

Walked row by row at the end of Phase 8 and again after the Phase 9 bonuses (2026-10-11). Proof = where to look.

| id | requirement | proof | result |
|---|---|---|---|
| A1 | Parse HTML + PDF, keep page structure | tests/test_pdf_parser.py, tests/test_html_parser.py; `python main.py parse --bid data/Bid1` | PASS |
| A2 | Extract tables correctly | tests/test_chunker.py (table cases), tests/test_pdf_parser.py ruled table | PASS |
| A3 | Clean headers/footers, broken lines, hyphenation, whitespace | tests/test_cleaner.py; Bid1 RFP: 0 filler lines, footer removed | PASS |
| A4 | Metadata: bid_id, file_name, doc_type, addendum_number, page, doc date | tests/test_classifier.py; chunk payloads in `/chunks/{id}` | PASS |
| A5 | Scanned/empty/unreadable files handled + logged (OCR bonus) | tests/test_ingestion_failures.py (empty, corrupt, encrypted, scanned, unknown ext, duplicate, OCR path) | PASS |
| B1 | RFP-suited chunking with overlap, justified in README | tests/test_chunker.py; README "Chunking" | PASS |
| B2 | Embeddings | `python main.py index --bid data/Bid1` (bge-small) | PASS |
| B3 | Vector store with metadata | tests/test_search.py (Qdrant payload filters) | PASS |
| B4 | Incremental indexing | tests/test_incremental.py; second `index` run adds 0 chunks | PASS |
| B5 | Hybrid search (dense + BM25) merged with RRF | config `hybrid`; rfp_intel/search/fusion.py | PASS |
| B6 | Metadata filtering ("only search Bid2 specs") | tests/test_search.py, tests/test_query_expansion.py; CLI check returns only the specs file | PASS |
| B7 | Re-ranking (cross-encoder) | config `hybrid_rerank`: Recall@5 1.000 | PASS |
| B8 | Query rewriting / expansion | tests/test_query_expansion.py (synonyms.yaml) | PASS |
| B9 | Citations: file, page, chunk text | `/search` hits carry the full Chunk; FieldValue.sources | PASS |
| B10 | REST API + CLI (`POST /index`, `GET /search`, `POST /ask`) | tests/test_api.py, tests/test_remove_bid.py; live curl of all 14 endpoints (incl. `DELETE /bids/{id}`) | PASS |
| B11 | >= 15 eval questions, Recall@k + MRR, >= 2 configs, table in README | 20 questions, 3 configs, eval/results/retrieval.md, README | PASS |
| C1-C7 | Orchestrator, Ingestion, Retrieval, Extraction, Addendum Reconciliation, Validator, Q&A/Report agents | rfp_intel/agents/nodes/*; logs/example_trace.jsonl (bonus: compare.py, go_no_go.py) | PASS |
| C8 | Framework choice explained | README "Agent framework and roles" | PASS |
| C9 | Shared state object | rfp_intel/agents/state.py GraphState | PASS |
| C10 | Validator feedback loop with max retries | trace shows retry rounds and every support verdict; tests/test_graph.py retry tests; tests/test_validator_corrections.py (partial list support is corrected, not retried) | PASS |
| C11 | Parallel agents where possible | three `retrieve` steps start within 10 ms (Send fan-out); tests/test_graph.py | PASS |
| C12 | Structured outputs for all agent messages | rfp_intel/schemas.py; tests/test_schemas.py; llm.structured() | PASS |
| C13 | Guardrails: "Not found in documents", >= 1 citation per value | tests/test_validator_rules.py, tests/test_baseline.py; outputs: 0 values without citation | PASS |
| C14 | Observability: per-step input, tool calls, output, tokens, latency; example trace | rfp_intel/logging_utils.py; logs/example_trace.jsonl | PASS |
| C15 | Error handling: LLM timeout, bad JSON, empty retrieval | tests/test_error_handling.py (also wrong API key, broken engine) | PASS |
| C16 | Extraction mode | `python main.py --bid ./data/Bid1` | PASS |
| C17 | QA mode incl. the 5 example questions | eval/qa_log.md (12 answers, brief's five first) | PASS |
| D1-D4 | 20 fields; value + sources + confidence + notes; addendum_changes + validation counts; due date with time + zone after addendums (`YYYY-MM-DD HH:MM TZ`, wording kept in notes) | outputs/Bid1.json, outputs/Bid2.json | PASS |
| T1 | Python 3.10+ | developed on 3.12; requirements.txt | PASS |
| T2 | Keys from env | .env only; config never prints keys | PASS |
| T3 | One command | `python main.py --bid ./data/Bid1` | PASS |
| T4 | Modular | rfp_intel/ingestion, search, agents, api | PASS |
| T5 | Unit tests | `pytest -q`: 338 tests; frontend: 33 Vitest tests; `python -m eval.smoke_e2e`: 23 end-to-end checks (docs/TESTING.md) | PASS |
| T6 | Unseen bid works | data/MiniBid run with no code changes; no bid-specific strings in source | PASS (no real Bid3 available; see ASSUMPTIONS 78) |
| G1 | Repo | https://github.com/Akshay0545/Emplay-Assignment (based on https://github.com/abhisheksaini3232/rfp_intel) | PASS |
| G2 | README | README.md | PASS |
| G3 | Architecture diagram | docs/ARCHITECTURE.md (overview, sequence, messages, processes); README Mermaid diagram | PASS |
| G4 | JSON per bid | outputs/Bid1.json, outputs/Bid2.json | PASS |
| G5 | Retrieval report | eval/results/retrieval.md | PASS |
| G6 | Q&A log (>= 10) | eval/qa_log.md (12) | PASS |
| G7 | Agent trace | logs/example_trace.jsonl | PASS |
| G8 | Demo video | TODO: record and link your own demo | TODO |
| X1 | Bonus: web UI | frontend/ (lint, test, build pass; verified against the live API) | PASS |
| X2 | Bonus: OCR | ingestion.ocr.enabled path implemented and tested with a stubbed engine; needs Tesseract installed | PASS (partial: no Tesseract on the dev machine) |
| X3 | Bonus: comparison agent | rfp_intel/agents/nodes/compare.py; `python main.py compare Bid1 Bid2`; POST/GET /compare; web Compare page; outputs/comparison.md; tests/test_bonus_agents.py | PASS |
| X4 | Bonus: go/no-go | config/capabilities.yaml + rfp_intel/agents/nodes/go_no_go.py; `python main.py go-no-go --bid Bid1`; POST/GET /bids/{id}/go-no-go; web Go / no-go tab; outputs/Bid1_go_no_go.md, Bid2_go_no_go.md | PASS |
| X5 | Bonus: semantic cache + cost tracking | rfp_intel/search/answer_cache.py (`cache.enabled`, `Answer.cached`, `cache stats/clear`); tokens + cost per step in every trace, `RunStatus.summary`, GET /metrics; tests/test_answer_cache.py | PASS |
| X6 | Bonus: Docker + CI | Dockerfile, frontend/Dockerfile, docker-compose.yml (Qdrant + API + web); .github/workflows/ci.yml (pytest, frontend lint/test/build, retrieval eval with a recall@5 floor) | PASS |
