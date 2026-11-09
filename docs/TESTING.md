# Testing guide

Three layers, from cheapest to most realistic. All commands run from the
project root with the virtualenv active.

| Layer | Command | Needs | What it proves |
|---|---|---|---|
| Unit + integration (fakes) | `pytest -q` | nothing (no network, no models, no key) | 302 tests: parsers, cleaner, classifier, chunker, search engine with fake models, graphs with a scripted LLM, validator rules, API, CLI, error handling, deployment switches, comparison and go/no-go agents |
| Frontend | `cd frontend && npm run lint && npm test && npm run build` | Node 18+ | strict TypeScript, 27 Vitest tests (components, hooks, Compare page, Go/no-go panel), production build |
| End-to-end smoke | `python -m eval.smoke_e2e` | API running on :8001, `.env` with an LLM key, bids indexed and extracted | 23 checks against the real engine, LLM and bids: every API endpoint and CLI mode, citations on every value, incremental indexing, the three retrieval configs, filters, the answer cache, "Not found" guardrail, background runs with traces, comparison, go/no-go, files on disk |

The evaluations are separate commands with their own reports:
`python main.py eval retrieval` (Recall@k / MRR per config, no LLM) and
`python main.py eval extraction` (field accuracy against gold answers, uses
the LLM judge).

## Running the smoke test

```bash
# terminal 1
uvicorn rfp_intel.api.server:app --port 8001
# terminal 2
python -m eval.smoke_e2e                         # default: http://localhost:8001, Bid1 Bid2
python -m eval.smoke_e2e --skip-llm              # only the checks that make no LLM call
python -m eval.smoke_e2e --api http://localhost:8000 --bids Bid1 Bid2
```

It prints one `[PASS]` / `[FAIL]` line per check, writes
`eval/results/smoke_e2e.md` and exits 1 on any failure. CLI checks that would
open the embedded vector store (index, search, ask, the evals) are exercised
through the API instead, because the API process holds the store; `parse`,
`compare`, `go-no-go` and `--help` run as real subprocesses.

## How the unit tests avoid the network

- `tests/graph_fakes.py`: `FakeEngine` (a three-chunk corpus with an addendum
  override) and `ScriptedLLM`, which returns valid Pydantic outputs for every
  structured call (`FieldExtractions`, `ReconcileDecisions`, `SupportChecks`,
  `QAPlan`, `AnswerDraft`, `ComparisonDraft`, `CriterionChecks`) and can be
  told to fail, to reject an answer once, or to return chosen criterion verdicts.
- `tests/search_fakes.py`: `FakeEmbedder` / `FakeReranker` so the real
  `SearchEngine`, Qdrant (embedded, in a temp dir) and BM25 run without models.
- `tests/fixtures/make_mini_bid.py` generates the synthetic bid folder
  (`tests/fixtures/mini_bid`, also copied to `data/MiniBid`) used by the
  ingestion, CLI and unseen-bid tests.

## Where each requirement is tested

`docs/CHECKLIST.md` maps every requirement of the brief to its test file or
command; `tests/test_bonus_agents.py` covers the comparison and go/no-go
bonuses (rows and `differs`, highlight citations, rule-decided criteria,
the recommendation logic, LLM failure handling, CLI exit codes and API
status codes).

## CI

`.github/workflows/ci.yml` runs on every push and pull request: `pytest -q`
(CPU torch wheel), the frontend lint / test / build, and the retrieval
evaluation on the shipped bids with a floor of recall@5 >= 0.9
(`python main.py eval retrieval --min-recall5 0.9`); the retrieval report is
uploaded as a build artifact.
