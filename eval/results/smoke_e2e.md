# End-to-end smoke test

Generated 2026-10-11T13:25:46+00:00; API http://localhost:8001; bids Bid1, Bid2; 23/23 checks passed.

| check | result | detail | seconds |
|---|---|---|---|
| API: health lists the bids as indexed | PASS | indexed ['Bid1', 'Bid2', 'MiniBid'] | 4.0 |
| API: GET /bids | PASS | Bid1: 4 files, Bid2: 5 files | 3.2 |
| API: GET /bids/{id} documents and chunks | PASS | Bid1: 4 docs, 210 chunks; Bid2: 5 docs, 38 chunks | 6.6 |
| API: POST /index is incremental (0 chunks added) | PASS | Bid1: unchanged, 210 chunks; Bid2: unchanged, 38 chunks | 6.5 |
| API: search in all three retrieval configs | PASS | vector: 5 hits; hybrid: 5 hits; hybrid_rerank: 5 hits | 13.8 |
| API: search metadata filter and bad config | PASS | doc_type=addendum: 5 hits; bad config -> 400 | 11.4 |
| API: chunk lookup, file serving, path traversal rejected | PASS | chunk 451b4bcdc71c1838c49d76d319c75bb53d198259:1:0 and file served; traversal rejected (400) | 16.9 |
| API: saved records have 20 fields, every value cited | PASS | Bid1: {'passed': 15, 'failed': 1, 'not_found': 4}; Bid2: {'passed': 14, 'failed': 3, 'not_found': 3} | 6.4 |
| API: eval results endpoints | PASS | 20 questions, best recall@5=1.000; extraction bids=2 | 6.7 |
| API: oversized question rejected | PASS | oversized question -> 422 | 3.2 |
| API: upload a throwaway bid, then DELETE /bids/{id} | PASS | uploaded 4 files (210 chunks), removed; second DELETE -> 404 | 175.1 |
| CLI: --help lists every command | PASS | all commands listed | 2.8 |
| CLI: parse writes outputs/parsed | PASS | 4 parsed files | 106.4 |
| API: POST /ask with citations | PASS | 'The submission deadline after all addendums is July 9, 2024 at 2:00 PM CST. [E1]' (1 citations, cached=True) | 3.1 |
| API: semantic answer cache hit | PASS | cache hit in 3.3s | 6.5 |
| API: unanswerable question -> Not found | PASS | unanswerable question -> Not found, no citations | 18.9 |
| API: background question with trace and summary | PASS | served from the answer cache (no graph steps); run `python main.py cache clear` for a cold run | 6.4 |
| API: comparison agent | PASS | 19 differing rows, 9 highlights | 14.7 |
| API: go/no-go agent | PASS | Bid1: review (6 met/0 not/1 unknown); Bid2: review (6 met/0 not/1 unknown) | 18.5 |
| CLI: compare | PASS | table printed; outputs/comparison.md rewritten | 18.6 |
| CLI: go-no-go | PASS | report printed; unknown bid -> exit 2 | 20.3 |
| API: runs list and metrics | PASS | 4 runs; cache hits=4 misses=1 | 5.5 |
| Outputs on disk | PASS | 8 files present | 0.0 |
