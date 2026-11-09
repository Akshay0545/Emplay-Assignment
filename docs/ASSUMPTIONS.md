# Assumptions and decisions not covered by BUILD_PLAN.md

Each entry: decision, reason, phase it was made in.

## Phase 0

1. **Repository root.** The plan's layout uses `rfp-intel/` as the root; this
   repository's root folder (`task_job/`) plays that role.
2. **Input locations.** The bid folders arrived under `docs/data/` and were
   moved to `data/Bid1` and `data/Bid2`. The brief was renamed from
   `Assignment-AI Engineer.pdf` to `Assignment-AI_Engineer.pdf` to match CLAUDE.md.
3. **Python 3.12.** The plan names Python 3.11; the machine has 3.12.10, which
   satisfies the brief's "Python 3.10+" (T1). All pinned packages support 3.12.
4. **Windows venv activation.** CLAUDE.md shows `source .venv/bin/activate`;
   on Windows it is `.venv\Scripts\activate` (PowerShell/cmd) or
   `source .venv/Scripts/activate` (Git Bash). The README will list both.
5. **LLM provider and models.** `LLM_PROVIDER=openai`,
   `EXTRACT_MODEL=gpt-6-luna` (high-volume extraction, low cost),
   `REASON_MODEL=gpt-6-sol` (validation, reconciliation, Q&A, summary). Both
   were verified live to return valid strict JSON-schema output. Model names
   live only in `.env`; changing them needs no code change.
6. **Temperature.** `llm.temperature: 0` stays in config, but these reasoning
   models reject any temperature other than the default. `llm.py` sends the
   configured temperature and, if the API rejects it as unsupported for that
   model, retries once without it and remembers the model for the rest of the
   process. Consequence: outputs are not guaranteed to be identical between
   runs; the validator, citations check and `LLM_DEV_CACHE` reduce the impact.
7. **`llm.max_tokens` 2000 -> 4000.** Reasoning models count hidden reasoning
   tokens against the output limit, so 2000 risked truncated JSON on large
   field groups. OpenAI receives it as `max_completion_tokens`.
8. **Cost table filled.** `cost.usd_per_1k_input/output` contain published
   prices for gpt-6-luna ($0.10 / $0.50 per 1M) and gpt-6-sol ($2 / $10 per 1M)
   so every StepLog carries real cost. Unknown models cost 0.
9. **Structured output mechanism.** OpenAI: `response_format` with a strict
   `json_schema`. Anthropic: `output_config.format` JSON schema rather than a
   forced tool call, because forced `tool_choice` is rejected by current
   Claude models. Both are "JSON-schema mode" as allowed by Section 6.3.
10. **Strict schema conversion.** Strict mode needs every property required
    and no extra keys, so `llm.strict_json_schema()` derives a strict copy of
    the Pydantic schema (all properties required, `additionalProperties:
    false`, defaults and numeric/length constraints removed; Pydantic still
    enforces them on the reply). Models with open-ended dicts (e.g.
    `ValidationResult.checks`) cannot be strict; for those the schema is put
    in the system prompt and non-strict JSON is validated as usual. The
    models in `schemas.py` are unchanged from Section 5.
11. **`Usage` lives in `schemas.py`** (rule: all Pydantic models there).
    `LLMError` is an exception, not a model, and lives in `llm.py`.
12. **Settings models live in `config.py`.** The typed config sections
    (`Settings`, `FieldSpec`, `IngestionPatterns`, ...) describe YAML files,
    not messages between modules, so they stay with the loader.
13. **Dev-cache key** is sha256 of model + schema name + system prompt +
    prompt (the plan says prompt + model); including the system prompt and
    schema avoids returning a stale answer when only those change. Cached
    calls report zero tokens and cost because nothing was spent.
14. **Not-yet-implemented CLI commands** print which phase delivers them and
    exit with code 1, so scripts cannot mistake a placeholder for success.
15. **`pytest.ini`** (not in the layout) sets `pythonpath = .` and
    `testpaths = tests` so `pytest -q` works from the root without installing
    the package.

## Phase 1

16. **Eval models live in `eval/loaders.py`** (`RetrievalQuestion`,
    `GoldField`). They describe eval files, not messages between
    `rfp_intel` modules, and `eval/` is outside the package.
17. **Gold file shape.** `eval/gold/<bid>.json` maps each of the 20 field
    names to `{value, must_contain, status?, scoring?, notes?}`.
    `status` defaults to `ok`; `scoring: faithfulness` marks Bid Summary
    (judged for faithfulness, not exactness).
18. **Matching ignores case and all whitespace** (`normalize()` removes
    whitespace), so "16 GB" matches "16GB". Retrieval relevance and gold
    tokens use the same rule.
19. **Bid2 Installation gold is `not_found`.** The plan says "Not required
    (none stated)"; since nothing is stated in the documents, the
    guardrail rule (missing info = not_found) applies.
20. **`must_contain` tokens are short and as written in the source**, and
    `eval/verify_eval_data.py` checks every token exists in the bid's files.
    Date format differences (e.g. "July 9, 2024" vs "2024-07-09") are left to
    the Phase 5 scorer and LLM judge.

## Plan corrections

21. **`config/fields.yaml` YAML fix.** The `contact_info` description in
    Section 4 ("Procurement contact(s): name, email, phone. ...") contains
    `": "` in an unquoted value, which YAML rejects. The value is now quoted;
    the text is unchanged.
22. **Numeric search hints.** YAML parses the search hint `1295` (Texas
    Form 1295, in the Additional Documentation field) as an integer;
    `FieldSpec` converts every search hint to text instead of editing the
    plan's YAML.

## Phase 2

23. **PDF lines are rebuilt from spans** instead of plain
    `get_text("text", sort=True)`. Sorted text glued side-by-side columns
    together (`210-BLYZDell Latitude 5550`), which breaks BM25 on part
    numbers. Spans are grouped into rows by vertical position and joined
    with a space at visible horizontal gaps; a vertical gap larger than 0.8
    line heights becomes a paragraph break.
24. **Drop selectors spare containers of fields.** Portal pages wrap all
    content in a `<form>`; an element from `html_drop_selectors` is removed
    only if it contains none of the configured labels.
25. **HTML label value** = text of the label element's following siblings,
    stopping at the next sibling that holds another label (so "Contact
    Information" does not swallow "Description"); else the next string.
26. **Classification runs on the raw first page, before cleaning**, because
    the cleaner removes repeated headers such as "ADDENDUM No. 1". The file
    name is checked against all patterns first, then the first page text.
27. **Repeated header/footer detection** treats digits as interchangeable
    ("Page 3 of 40" = "Page 4 of 40"), needs >= 2 non-empty pages and >= 50%
    of them, and ignores lines shorter than 4 characters or without letters
    (so "Yes" or "50,000" are never removed). One-page documents are untouched.
28. **Document date fallback skips deadlines.** When no "Issue Date"/"Date:"
    is found, the first date in the first 500 chars is used unless the same
    sentence has a deadline word just before it (due, deadline, closing,
    extend..., no later than). Addendum 2 therefore has no doc_date rather
    than the due date.
29. **Title** = HTML `Title` field; for PDFs the metadata title (minus
    "Microsoft Word - " and extensions), else the first line with >= 3 words
    that is <= 20% digits (skips addresses and phone numbers).
30. **Failure reporting.** A file that cannot be parsed (empty, corrupt,
    password-protected) becomes a `Document` with no pages and a warning, so
    it is visible downstream; unsupported extensions and duplicate files are
    skipped and logged, and `parse` lists them as "skipped".
31. **`parse_pdf` / `parse_html` take an optional `warnings` list** in
    addition to the planned `(path)` argument, so non-fatal problems reach
    `Document.warnings`.
32. **Bid1 data notes.** The main RFP has 62 pages (the plan says 40; the
    RFP body is 40 pages plus attachments). Pages 54-59 are blank with no
    images, so they are flagged empty and OCR could not help. Spec lists can
    continue across a page break (Tier 3 runs from page 4 to 5); the Phase 3
    chunker must handle that. The portal shows Closing Date 03:00 PM EDT,
    which equals Addendum 2's 2:00 PM Central.

## Phase 3

33. **Chunking runs over each whole document**, not page by page, so Q&A
    pairs and spec lists that cross a page break stay whole; `page` is the
    page a chunk starts on (a cited quote may continue on the next page).
34. **Running headings inside a spec list** (heading lines at the top of a
    page followed by more bullets) are skipped, so the list continues.
35. **Q&A units** end at the next question number (n + 1) or a paragraph
    break after "Answer:"; page breaks do not end a unit, and a list that
    restarts at "1." inside an answer belongs to the answer.
36. **Only real tables become table chunks** (>= 50% of rows with 2+
    non-empty cells); layout grids are skipped because their text is already
    in the page text. Table/text duplication is accepted.
37. **min_tokens applies to every kind except qa and record** (plan rule);
    a heading only starts a new text chunk once the current one has
    min_tokens; a tiny tail joins the previous chunk; exact duplicate chunks
    within a document are dropped (20 empty response-form tables in Bid1).
38. **Uppercase heading rule** also needs letters to be >= 50% of visible
    characters, so date lines such as "06/01/2024 at 2:00 PM EDT" are not
    headings. Numbered bullets need whitespace after "1." / "a)" so "2.1 Line
    Information" is a heading, not a bullet.
39. **BM25 tokenizer** keeps IDs whole and also emits their parts
    ("ja-207652" + "ja" + "207652"); a trailing "#" is kept ("si#"). BM25
    returns chunks containing a query token ranked by score (not score > 0:
    small corpora give IDF 0 to terms in half the chunks). Multi-bid search
    builds one BM25 over the selected bids at query time.
40. **Doc-type filters from query text need scoping words** ("only search
    ... specs", "in the addendum", "from the RFP", "specs only"); a bare
    mention is a topic. Bid ids and "addendum N" always filter.
    `parse_filters` returns `(filters, cleaned_query)`. Explicit filters win.
41. **Query expansion** returns the query plus up to 3 synonym variants; each
    variant runs through vector and BM25 search and all lists are fused with
    RRF. No LLM rewriting (deterministic, free); widened retries in Phase 6.
42. **Vector point ids and fusion keys include the bid id**
    (`<bid>/<chunk_id>`): the plan's chunk_id comes from the file hash, and
    the same file in two bids would otherwise overwrite each other.
43. **`IndexStats`** (used but not defined by the plan) added to schemas.py.
    The manifest stores file hashes plus a hash of the chunking settings and
    embedding model, so changing those re-indexes automatically.
44. **Models load from the local Hugging Face cache first** and download only
    when missing (no network check on every run).

## Phase 4

45. **Retrieval eval runs every question with its bid filter** and
    top_k = max(eval.recall_k) = 10 for each config; the first relevant rank
    gives Recall@k and MRR. Reports: eval/results/retrieval.md + .json.
46. **Vector size cached in the manifest** (`_dim`) so an unchanged bid can be
    skipped without loading the embedding model (~1 s instead of ~15 s).

## Phase 6a

47. **Parallel fan-out uses a `field_group` branch node** that runs the
    sub-graph `retrieve -> extract_<group>` for one Task sent with
    LangGraph `Send`. The branch returns only reducer keys (evidence, drafts,
    usage, errors), because three parallel branches writing plain keys
    (run_id, plan, ...) in one step is an invalid update in LangGraph. The
    retrieve node is still the only search caller and every extractor step
    is preceded by a retrieve step in the trace.
48. **Retrieve also collects addendum-only evidence per field**
    (key "<field>::addendum", top 4) so the Reconciler never calls search.
    Q&A evidence uses the key "qa::<bid>".
49. **Reconciler makes one batched LLM call** for all fields that have
    addendum evidence (instead of one per field) to keep REASON_MODEL cost
    low; decisions are applied in addendum order so the latest wins.
    Additions on a null field become the value; clarifications keep the
    value and add the citation and a note.
50. **Validator LLM support check is one call per field group**; if that
    call fails the fields are treated as supported and the error is logged
    (a timeout must not fail every field). Rule failures (citation, quote,
    format, consistency) always count.
51. **Not_found confidence** = 0.8 when the field's evidence plus addendum
    evidence had >= 6 passages, else 0.5 (plan rule, "relevant passages
    checked" = passages retrieved for the field).
52. **GraphState gains `widened: bool`**; the branch state adds `task`.
    Both are needed to route widened retries; everything else matches the
    plan's GraphState.
53. **Extraction node code is shared with the baseline** (`agents/baseline.py`
    `extract_group`, citation mapping, quote verification), so graph vs
    baseline accuracy differences come from retrieval, reconciliation and
    validation only.
54. **Prompts live in agents/prompts/*.md with {placeholders}** filled by
    string replacement (not str.format) so braces in evidence text are safe.
55. **Q&A validation** = quote checks + one LLM support check; an
    unsupported answer gets one widened retry, then becomes
    "Not found in documents" rather than an unsupported claim.

## Phase 5

56. **Baseline queries** per field = the first sentence of the description
    plus the search hints split into two comma-joined halves (2-3 short
    queries). Hits from all queries are merged per field, best re-rank score
    first, and capped at `agents.evidence_top_k` so a group prompt stays
    small; evidence is then de-duplicated across the group's fields and
    numbered E1.. in first-seen order.
57. **Stored quotes are verbatim chunk spans.** The model's quote is located
    in the chunk ignoring case and whitespace (`find_span`) and the exact
    original span is stored, so the validator's quote_in_chunk check and the
    page lookup use the document's own text. A quote not found in the chunk
    falls back to the chunk's first 300 characters. Evidence ids are read
    leniently ("E3", "e3", "[E3]", "3"); unknown ids are dropped.
58. **Baseline failure values.** A group whose LLM call fails gets every
    field as status `failed`, value null, confidence 0.0 and the error in
    notes; a value the model returns with no valid evidence id keeps the
    value with status `failed`, confidence 0.2, note "no citation"; an empty
    string / empty list from the model counts as not_found.
59. **Baseline Bid Summary** uses an inline prompt in `agents/baseline.py`
    (REASON_MODEL, ok fields only). Its citations are the union of every ok
    field's citations (so a reader can trace each sentence), confidence 0.6.
    The graph's report node keeps its own `prompts/summary.md`.
60. **Extraction-eval models** (`JudgeVerdict`, `FieldScore`, `BidResult`)
    live in `eval/run_extraction_eval.py`, like the eval loaders (item 16);
    the judge call still goes through `llm.structured`.
61. **Scoring rules not spelled out by the plan:** gold not_found vs output
    null (not_found or failed) = exact; gold with a value vs output null =
    wrong, even when the gold value is the word "None" (Bid2 Pre Bid
    Meeting); a failed judge call scores partial; lists are joined with
    "; " before token matching.
62. **Faithfulness judging of Bid Summary** compares the summary with a
    digest of all gold field values (its own gold value is null); the result
    is shown but excluded from accuracy.
63. **Citation page = chunk start page.** Chunks can span a page break (item
    33), so a fallback quote (chunk start) can run onto the next page; e.g.
    the Bid1 contact_info citation on p.1 of the RFP continues on p.2. The
    Phase 6 validator checks quotes against chunk text, not page text.

## Phase 7a

56. **API error bodies always use `{"error": str, "run_id": null}`**, including
    HTTP 4xx, request validation (422) and unhandled exceptions (500); `run_id`
    is always null because no endpoint fails mid-run (background runs report
    failure through `GET /runs/{run_id}` instead).
57. **One background extraction at a time.** `services.start_extraction` holds a
    process-wide lock (single Qdrant local client, CPU-bound embedding); a
    second `POST /extract` while one runs returns 409. Run state lives in an
    in-memory registry; after a restart, `GET /runs/{id}` falls back to the
    trace file and reports "done" when the last step is `report` /
    `validate_answer` without error, else "failed".
58. **`GET /bids` reads headline values from the saved record**
    (`outputs/<bid>.json`: Title, company_name, Due Date; list values joined
    with "; ") and `due_date_changed_by` is the addendum number of the latest
    "override" of "Due Date" in `addendum_changes`. Bids are the directories
    under `data_dir`; `indexed` comes from the engine manifest.
59. **`GET /bids/{bid_id}` prefers `outputs/parsed/<bid>/*.json`** (written by
    `main.py parse`) and re-parses the folder with `load_bid_folder` only when
    no parsed JSON exists; `chunks` is the BM25 corpus size of the bid.
60. **`POST /index` accepts a folder path or a bid id** (resolved under
    `data_dir`), 404 when neither exists. `POST /bids` validates bid_id against
    `^[A-Za-z0-9_-]{1,64}$`, accepts only .pdf/.html/.htm and rejects any file
    name containing a path separator before writing anything.
61. **`GET /files` resolves the real path and requires it to stay inside
    `data_dir/<bid_id>`**; a traversal (`..%2F.env`, `../.env`) is a 400 or a
    404, never a file outside the bid folder. PDFs are served inline as
    `application/pdf`, HTML as `text/html`.
62. **Added API-only schemas** `BidSummary`, `DocumentInfo`, `BidDetail` to
    `schemas.py` (the plan defines the response columns but not the models).
63. **The API lifespan does not replace an engine already injected with
    `set_engine`** (it just calls `get_engine()`), so tests run the real
    lifespan against `FakeEngine`.

## Phase 7b

64. **shadcn/ui components are hand-written** under `frontend/src/components/ui`
    (button, card, badge, tabs, table, sheet, dialog, input, select, skeleton,
    tooltip, sonner) using the same Radix primitives, Tailwind tokens and
    `cn()` helper the shadcn CLI generates; `components.json` is kept so
    `npx shadcn add` works later. The CLI's interactive init was skipped to
    keep the build non-interactive on Windows.
65. **Frontend pins React 18 / Vite 5 / TypeScript 5.6 / ESLint 9** (the
    current `create-vite` template ships React 19, Vite 8, TS 6 and oxlint,
    which the plan does not ask for). The ESLint config is the stock
    create-vite flat config (`@eslint/js` + `typescript-eslint` recommended +
    react-hooks + react-refresh); the only per-line disables are the standard
    shadcn pattern of exporting `buttonVariants`/`badgeVariants` and a context
    hook beside its provider.
66. **`src/api/types.ts` is the hand-written source of truth** mirroring
    `rfp_intel/schemas.py` plus the API-only shapes (`BidSummary`,
    `DocumentSummary`, `BidDetail`, eval payloads). `npm run types` generates
    `src/api/schema.ts` from `/openapi.json` once the API is up; that file is
    git-ignored from lint and not imported yet.
67. **A 404 from `GET /bids/{id}/record`, `/eval/retrieval` and
    `/eval/extraction` is a normal "nothing yet" state** (rendered as an empty
    state with a next action), not an error; every other non-2xx is thrown as
    `ApiError(message = body.error)` and shown as an error state or toast.
68. **Citations carry no bid id.** When an `Answer` spans several bids the
    drawer resolves the bid for the "Open PDF at page" link via
    `GET /chunks/{chunk_id}` (`Chunk.bid_id`); with a single bid in scope the
    bid is known and no extra call is made. Search hits use `Chunk.bid_id`.
69. **Key-facts grid and header read the field names from
    `config/fields.yaml` by name** (`Bid Number`, `Title`, `Due Date`,
    `Bid Submission Type`, `Term of Bid`, `Pre Bid Meeting`, `contact_info`,
    `company_name`, `Bid Summary`). These are configuration keys, not bid
    data; a renamed field simply shows "Not found in documents" in the grid.
70. **"Run extraction" after upload** navigates to `/bids/<id>?extract=1`
    from the toast action; the bid page starts the run once and strips the
    flag. A 409 from `POST /extract` is shown as a toast. Polling of
    `/runs/{id}` runs every 1.5 s and stops on `done`/`failed`, then the
    record and bid list are refetched.
71. **The `/search` `addendum` filter is sent only when set**; `top_k` is
    fixed at 10 and `config` defaults to `hybrid_rerank`. The Evaluation
    charts assign one fixed colour per config (`vector`, `hybrid`,
    `hybrid_rerank`, extra configs appended alphabetically, max 3 series);
    the palette was validated with the dataviz colour checks in both modes.
72. **The five suggested questions in Ask are the brief's verbatim text**
    (`frontend/src/lib/questions.ts`); this is the only place the source tree
    mentions a bid name, as allowed by the plan.

## Phase 6a (after the first real run)

73. **Date-format rule applies to Due Date and Pre Bid Meeting only.**
    Delivery Date is a window or statement ("within 45 days of award", "no
    fixed date; quotes per project"), which the first run wrongly rejected,
    causing two retry rounds and a lost value.
74. **Reconciler decisions carry `old_value`.** When the extractor already saw
    the addendum (Due Date came out as July 9 directly), the reconciler is
    told to still report an override and take the earlier value from the
    draft's notes/evidence, so `addendum_changes` records the June 27 ->
    July 9 override rather than an "X -> X" clarification. Clarifications
    whose text equals the current value are ignored.
75. **One change per (field, kind, addendum)**: a reconcile rerun after a
    validator retry replaces its earlier entry instead of appending a
    reworded duplicate.
76. **Validation reasons go into the trace** (`validation` tool call on the
    validate step: passed count, failed fields with reasons, to_retry) so a
    retry can be diagnosed from logs alone.
77. **Fewer searches per field** (2 queries + 1 addendum query instead of
    3 + 2): each hybrid search costs ~3-5 s on CPU (embedding, BM25,
    cross-encoder), and retrieval was 115-203 s per group in the first run.
    The widened retry still uses up to 5 queries with synonym expansion.

## Phase 8

78. **No unseen Bid3.** Only two bid packages exist (user: "we only have
    data for 2"). The "unseen bid without code changes" requirement (T6)
    is demonstrated by running the full pipeline (real models + real LLM)
    on the synthetic `tests/fixtures/mini_bid` folder copied to
    `data/MiniBid`, by the CLI end-to-end tests on that folder, and by the
    grep showing no bid-specific strings or secrets in source. The five
    suggested questions in the web app's Ask page are the brief's own
    examples and are the only place source mentions a bid by name.
79. **Scoring: a gold value that only says "None" / "N/A" / "Not stated"
    counts as absent.** An output of `not_found` (or an equivalent none
    phrase) scores exact against it; a real value scores wrong. Previously
    Bid2 "Pre Bid Meeting" (gold "None") was scored wrong for the
    guardrail-correct `not_found`. Both the graph outputs and the saved
    baseline snapshot (`eval/results/baseline/`) are scored with this rule
    (`run_extraction_eval(outputs_dir=..., results_dir=...)`).
80. **Graph vs baseline accuracy is run-to-run noisy** (same extraction
    code; reasoning models cannot be run at temperature 0): graph
    0.895/0.868 vs baseline 0.842/0.895 on Bid1/Bid2, i.e. the graph is
    +5.3 points on Bid1 and -2.7 on Bid2; its structural gains are the
    addendum change log, the validator (0 failed, every value cited and
    quote-verified) and the explicit retry trace, not raw field accuracy.

## Phase 6b

81. **Answer support check judges facts only.** The validator prompt is
    shared with field validation; for answers it gets extra rules so
    wording that restates the question ("after all addendums", "for each
    bid") needs no proof. Without this the brief's first question was
    rejected twice and became "Not found in documents".
82. **Change questions pull both sides of each recorded change.** When a
    question mentions an addendum/changes, the Retrieval agent adds queries
    derived from the bid's saved change log (old value -> original passage,
    new value -> addendum passage) and raises the request's top_k
    (6 + 2 per extra query, max 20) so neither side is cut by the merge.
83. **Q&A traces record the draft and the verdict** (`draft` and
    `answer_check` tool calls with the support reason) so a rejected answer
    can be diagnosed from the trace.
84. **Port 8000 was occupied on the development machine** by an unrelated
    local server; the live checks ran with `uvicorn ... --port 8001` and the
    web app can point at any port via `VITE_API_URL`. The documented default
    stays :8000.
85. **Fresh-clone verification** (new venv, `pip install -r requirements.txt`,
    `pytest`, `python main.py --bid ./data/Bid1` from an empty index) passed;
    that run ended with 2 fields in status `failed` (Bid Submission Type,
    Product Specification: cited and quote-verified, but the LLM support
    check judged the long composite values "not fully supported" after two
    retries). This is judge variance between runs (the committed run had 0
    failed). The validate prompt now states that paraphrase / merged RFP +
    addendum lists are acceptable when every factual claim is in the
    evidence; the plan's rule (keep value, status failed, low confidence)
    is unchanged.
86. **Cheaper reasoning model (2026-10-10).** `REASON_MODEL` moved from
    gpt-6-sol ($2 / $10 per 1M tokens) to gpt-5.4-mini ($0.75 / $4.50);
    `EXTRACT_MODEL` stays gpt-6-luna ($0.10 / $0.50). Measured on the Bid2
    trace (44k/7k extractor tokens, 32k/7k reasoning tokens) a run costs
    about $0.14 with gpt-6-sol, $0.06 with gpt-5.4-mini and $0.015 with
    gpt-6-luna for everything. gpt-6-luna as the reasoner was tried and
    rejected: Bid2 accuracy fell to 0.816 (Model_no / Part_no lost during
    reconcile + validate retries). gpt-5.4-mini keeps 0.895 on both bids.
    Its validator is more literal than gpt-6-sol's: a first run failed 8
    Bid2 fields and rejected a correct "Addendum 2 changed only the due
    date" answer because the inference was "not stated". `validate.md` now
    says to judge the value (not the notes), that qualifications which only
    describe the evidence are not added facts, and that a conclusion which
    follows directly from the evidence is supported; a field is unsupported
    only when a date, number, name, amount or requirement is absent or
    contradicted. With that prompt Bid2 validates 17 / 1 / 2 in 21 steps.
    The cost table in `config/settings.yaml` has the new model.
87. **Browser end-to-end pass of the web app (Playwright, 31 checks)**
    found and fixed three UI bugs: the Evaluation page expected
    `bids` as an object with a `summary` block while
    `eval/results/extraction.json` writes a list with the counts at the top
    level (the extraction section rendered empty); a citation chip inside a
    Fields-table row bubbled its click to the row, which re-opened the
    drawer without the active citation (the chip now stops propagation);
    search result cards were keyed by `chunk_id` alone, which collides when
    two bids contain the same file (chunk ids hash file content), so the
    key is now `bid_id:chunk_id`. The Playwright script lives outside the
    repo (it needs the live API and real LLM calls) and is not part of
    `npm test`.
88. **One extraction at a time is visible everywhere.** The API runs one
    extraction at a time (one Qdrant client, CPU-bound embedding). The web
    app only knew about runs it had started itself, so after a reload, or
    on another bid's page, "Run extraction" looked available and the 409
    surfaced as "already running for this bid" even when a different bid
    was running. Now `GET /runs` lists the runs of the API process (newest
    first), the 409 body names the running bid and carries its `run_id`,
    and the web app polls `/runs`: a running run for the open bid is
    adopted (progress card without clicking), a run for another bid
    disables the button as "Busy: extracting <bid>", and the toast shows
    the server's message. Runs are kept in memory, so an API restart
    forgets them; a trace file still answers `GET /runs/{id}`.
89. **Responsive web app (phone to wide desktop).** The shell no longer
    forces a 1024px minimum width. Below the `lg` breakpoint the sidebar
    is replaced by a hamburger that opens the navigation in a left-hand
    sheet (closed automatically on route change); the bid switcher and
    theme toggle stay in the sticky top bar. Page headers share one
    `PageHeader` component that stacks title, description and actions on
    phones. Grids collapse to one column (bids, overview, evaluation),
    forms stack (search query + four filters), tab lists and suggested
    questions scroll horizontally, and chat bubbles widen to ~90% on
    phones. Wide tables keep every column in the DOM but hide the
    lower-priority ones below a breakpoint (`hidden md:table-cell`):
    Fields hides Confidence below `md` and Citations below `lg` (the row
    click still opens the drawer with everything); Documents hides
    Addendum / Tables / Warnings; Evaluation hides Bid, Expected file and
    Reason. The Fields table uses fixed layout so the Value column takes
    the remaining width and long tokens wrap with `overflow-wrap:
    anywhere` instead of pushing the table past the viewport. Dialogs are
    inset 1rem on phones with a stacked footer; the citation sheet is
    full-width below `sm`. The evaluation bar chart no longer animates
    (it rendered empty in headless captures and prints). Checked with
    headless Chromium screenshots at 390, 768, 1024 and 1440px.
90. **Fast Q&A retrieval (same answers, one fused search per request).** A
    traced question took 137 s, 127 s of it in retrieval: the planner's 4
    queries plus 2 change-log queries for *every* recorded addendum change
    (16 searches), each search embedding up to 4 synonym variants and running
    the cross-encoder, and the validator's "widened" retry repeating the
    identical 16 searches because widening changes nothing for Q&A. Three
    changes, none bid-specific: (1) `SearchEngine.search_many(queries, ...)`
    runs vector + BM25 for every query and variant, fuses everything with one
    RRF and re-ranks once against the first query; `search` is the one-query
    case, so CLI, API and the retrieval eval are unchanged (eval scores are
    identical). Query embeddings are batched in one model call and memoised
    per query text (512 entries). The Retrieval agent issues one
    `search_many` per EvidenceRequest and records one `search` tool call with
    the `queries` list. (2) Change-log queries are added only for changes
    whose *field name* shares a content word with the planner queries (a
    stop-list removes question words, "addendum"/"bid"/"rfp" and generic
    words such as "date", "type", "number"); values are not matched because
    they are long and noisy. At most 3 changes (6 queries). The decision is
    recorded as a `change_log` tool call. (3) The Q&A validator's retry goes
    back to `answer`, not `retrieve`: the draft is rewritten on the same
    evidence with the validator's reason appended to the prompt
    (`answer_feedback` in the graph state). The extraction graph's widened
    retry is unchanged. Also: the API lifespan now calls `engine.warm_up()`
    so the embedding and re-ranking models load at startup (~30 s) instead
    of inside the first user request. The same question now runs in ~10 s
    warm (one retrieve step, ~6 queries, one re-rank).
91. **Questions run in the background with visible progress.** `POST /ask`
    still answers synchronously (CLI, eval, simple clients). The web app
    uses `POST /ask/runs` (202, `{run_id}`) and polls `GET /runs/{run_id}`,
    which now carries `kind` ("extract" | "qa"), the `answer` once a question
    is done and `error` when it failed. The run registry is shared by both
    kinds; `GET /runs` lists both and the web app's "busy extracting" state
    only considers extractions. Questions run on a pool of
    `api.ask_workers` threads (default 2) and never take the extraction
    lock; when `api.ask_queue_max` (default 8) questions are already waiting
    the API answers 429 and the chat shows a "busy" message. A question
    still running after `agents.qa_timeout_s` (default 120) is reported as
    failed; its thread cannot be interrupted, so it finishes in the
    background and its late result is discarded. Runs are kept in memory, so
    an API restart forgets them (a trace file still answers `GET /runs/{id}`,
    with the kind inferred from the nodes). The pending chat bubble shows
    the step the server is on (planning, searching, drafting, checking),
    elapsed seconds and a link to the trace; the trace page shows the answer
    bubble above the steps for question runs. `GET /health` reports the
    question queue (`running`, `queued`, `workers`).
92. **Deployment switches (Phase C).** `vector_store.kind` now accepts
    `qdrant_server` next to `qdrant_local`; the server location and key come
    from `.env` (`QDRANT_URL`, `QDRANT_API_KEY`), and
    `VectorStore.make_client` builds the right client, so several API
    processes can share one index. `embedding.provider: openai` adds
    `RemoteEmbedder` (OpenAI embeddings API, batched, normalized, using
    `LLM_API_KEY`) behind the same `EmbedderLike` protocol; it is an
    embedding call, not a completion, so it does not go through `llm.py`.
    The index manifest already keys on the embedding model name, so a
    provider switch re-indexes. `reranker.enabled` and
    `reranker.max_passages` let a deployment turn the cross-encoder off or
    cap it (with `enabled: false` the `hybrid_rerank` config behaves like
    `hybrid`). `embedding.torch_threads` (0 = default) sets torch's thread
    count once per process for the local models. A deployment changes these
    without editing the base file through `RFP_SETTINGS_OVERLAY=<yaml>`,
    deep-merged over `config/settings.yaml` (the shipped `config/docker.yaml`
    selects the server and 4 question workers); settings still live only in
    `config/*.yaml`. `Dockerfile`, `frontend/Dockerfile` (nginx serving the
    built app) and `docker-compose.yml` (Qdrant + API + web) are the
    reference stack. A second process opening the embedded store fails fast
    with a message that names the `qdrant_server` option.
93. **Semantic answer cache, run summaries and metrics (Phase D).** The
    cache lives in `rfp_intel/search/answer_cache.py` and uses a second
    Qdrant collection (`<collection>_answers`) in the same store, so it
    works in local and server mode without new infrastructure. Key = the
    question's embedding; a hit needs the same bid scope (sorted ids, or
    "*" for all bids), cosine >= `cache.similarity_threshold` and the same
    index fingerprint (a hash of the bids' manifest entries, so re-indexing
    invalidates). "Not found" answers are never cached because they can
    come from a transient LLM failure. The lookup happens in
    `services.ask`, outside the agent graph, which is why it may touch the
    engine (the "only the Retrieval agent searches" rule is about the graph
    nodes). `cache.enabled` is now true by default (BUILD_PLAN shipped it as
    false because it was a bonus); `Answer.cached` tells the client, and the
    CLI offers `cache stats` / `cache clear`. Run summaries are computed
    from the StepLogs (`summarize_run`) rather than appended to the trace,
    so trace files keep one shape; `RunStatus.summary` and one log line per
    finished run expose them. `GET /metrics` is plain JSON from in-process
    counters (no Prometheus dependency; an API restart resets it).
    `api.max_question_chars` (1000) rejects oversized questions with 422.

## Phase 9 (bonuses: comparison, go / no-go, CI)

94. **Comparison agent works from saved records, not from a new retrieval.**
    `python main.py compare Bid1 Bid2` (and `POST /compare`) reads
    `outputs/<bid>.json` for each bid; a bid without a record is an error
    that names the command to run (exit 2 / HTTP 404) rather than a silent
    extraction, because an extraction costs minutes and LLM calls and the
    user should start it knowingly. The rows (one per configured field, a
    cell per bid, `differs` = normalized values are not all equal, all-missing
    counts as equal) are built in code; the LLM writes only the narrative and
    the highlights from those validated values, and each highlight inherits
    the citations of the cells it names (an unknown field name is dropped).
    The plan named `outputs/comparison.md`; `comparison.json` is written next
    to it and `GET /compare` returns the latest one, so a second comparison
    replaces the first. Two or more bids are accepted.
95. **Go / no-go: criteria live in `config/capabilities.yaml`, the decision is
    code.** Each criterion lists the extracted fields it is judged on, the
    capability values, a rule in prose, `critical` and `when_not_found`. A
    criterion none of whose fields has a value is decided by `when_not_found`
    without an LLM call (bonding and contract vehicle default to met: a bid
    that states no bond or vehicle needs none; the others to unknown). One
    structured LLM call judges the remaining criteria from the field values
    only. The recommendation is deterministic: `no_go` when a critical
    criterion is not met, `review` when anything is unknown or a non-critical
    criterion is not met (an unknown critical criterion becomes `no_go` with
    `decision.unknown_critical: no_go`), else `go`. A third value, `review`,
    was added to the brief's go / no-go because an automatic "go" on unknown
    facts would be a guess. The shipped profile is a sample company
    (authorized for the common device manufacturers, 45-day delivery,
    bonding to $250k, a few served states and cooperatives) and is meant to
    be edited; nothing in it refers to a particular bid. Both bonus graphs
    are single-node LangGraph graphs so their runs are traced like the others
    (`GET /runs/{id}` reports kind `compare` / `go_no_go`).
96. **CI runs the retrieval evaluation, not the extraction one.** The
    extraction eval needs an LLM key and real runs; the retrieval eval needs
    only the local models and the shipped bids, so the workflow indexes Bid1
    and Bid2 and fails below `--min-recall5 0.9` (the best config scores
    1.0). torch is installed from the CPU wheel index first so the pinned
    requirement resolves without the CUDA build. The unit-test job sets a
    fake `LLM_API_KEY` because the wrapper checks for its presence; no test
    calls a provider.
97. **End-to-end smoke test (`eval/smoke_e2e.py`).** `pytest` runs with
    fakes, so a separate script checks every API endpoint and CLI mode
    against the real engine, LLM and bids and writes
    `eval/results/smoke_e2e.md`. CLI modes that would open the embedded
    store are checked through the API (the API process holds the store).
98. **LLM provider fallback.** `llm_fallback` in `config/settings.yaml`
    names a second provider (Anthropic by default: `claude-haiku-4-5` for the
    extractors, `claude-opus-5` for reasoning, the current generation per the
    Claude API reference; the model names are plain config). `rfp_intel/llm.py`
    routes to it when the primary provider has no key (`LLM_API_KEY` unset)
    or a call is rejected with an authentication / permission error, and the
    switch sticks for the process so a bad key is not retried on every call.
    Only key problems trigger it: timeouts, rate limits and server errors
    keep their existing handling, because switching providers mid-run for a
    transient error would double latency and mix model behaviour across one
    record. The fallback's key comes from `ANTHROPIC_API_KEY` /
    `OPENAI_API_KEY` in `.env` (`LLM_API_KEY` stays the primary's key), the
    cost table carries the Anthropic prices, the trace's LLM calls record the
    provider and the model actually used, and `GET /health` shows
    `llm_provider`. Tests fake both providers (`tests/test_llm_fallback.py`).
99. **Per-field evidence depth and hint coverage (extraction accuracy pass).**
    The extraction eval sat at 0.87 / 0.895 because list-type fields
    (required documents, delivery schedule, manufacturer requirements) were
    extracted from six passages shared across many sub-topics, so items that
    only appear once (business references, an attached affidavit form, a
    "coordinate deliveries with ..." answer) fell outside the evidence. Three
    changes, none bid-specific: (1) `config/fields.yaml` takes an optional
    `top_k` per field (`FieldSpec.top_k`), honoured by the Retrieval agent and
    the baseline; unset fields keep `agents.evidence_top_k`, and a widened
    retry never returns fewer passages than the normal pass. (2) Field queries
    are the field name with its first hints, a short description query, then
    the remaining hints in groups of three as separate queries (a long joined
    hint list dilutes BM25, so a passage matching one hint never surfaced);
    `search_many` now reserves a share of the re-rank pool for every query's
    own best candidates and re-ranks each candidate by its best cross-encoder
    score over all the request's queries, in one batched call
    (`Reranker.score_pairs`), instead of scoring against the first query only.
    Single-query search (the retrieval eval, `/search`) is unchanged. (3) Field
    descriptions state what the value is *not*: factory-configured options in a
    quote are not installation services, SI#/SKU codes are part numbers rather
    than model numbers, manufacturer-authorization requirements count for "MFG
    for Registration" when no brand is named, and "no fixed date" delivery
    schedules are described rather than reduced to a start month. The eval
    matcher (`eval/loaders.normalize`) also ignores hyphens, dashes, slashes
    and underscores, so "white-glove" / "W-9" / "Contract_Affidavit.pdf" match
    the gold tokens "white glove" / "W9" / "Contract Affidavit"; letters and
    digits still have to match exactly, so this is a normalisation fix, not a
    loosened test.
100. **Partial support of list values is corrected, not retried.** Both
    bids ended with long list fields (required documents, specifications)
    marked `failed` because one item out of many was not in the cited
    evidence, and a retry re-extracted the same list. The Validator's
    support check now returns `unsupported_items` per field (the items,
    verbatim, whose facts the evidence does not state); when a list value
    has some but not all items rejected, the node keeps the supported
    items, drops the rest and writes "Validator removed N unsupported
    item(s): ..." into the notes, and the field passes. A single-string
    value, a list whose every item is rejected, or a verdict that names no
    matching item still fails as before, so the correction cannot hide a
    genuinely unsupported field. Every support verdict (supported, reason,
    unsupported_items) is recorded as a `support_check` tool call in the
    trace, which the brief's observability requirement asks for and which
    earlier runs lacked (the reason strings were discarded). The critic sees
    list values as JSON arrays (a "; "-joined string made it copy the whole
    value as one "item"), a joined entry is split back into items as a
    fallback, and Product Specification is asked for as a list (one item per
    tier or component group) so that a single doubtful detail no longer
    fails a whole multi-tier specification.
101. **Due Date is normalized to `YYYY-MM-DD HH:MM TZ`.** The brief's
    example record shows the due date in that shape; the extractors keep
    returning the value as written (so quotes stay verifiable) and the
    Report agent applies `normalize: datetime` from `config/fields.yaml`
    (`FieldSpec.normalize`) after validation: `rules.format_datetime`
    renders the first date, the stated time and the zone as written
    ("Central" -> CT, "cst" -> CST); the original wording goes into the
    notes as "As written: ...". A value with no parsable date is left
    alone, and only Due Date uses it because Pre Bid Meeting and Delivery
    Date are often windows or processes rather than one instant. The gold
    `must_contain` tokens for Due Date follow the new format.
102. **Bids can be removed (`remove_bid`).** Uploaded test bids (two stray
    folders from web-app uploads) had no way out: they stayed in the
    manifest, both stores, `outputs/` and `data/`. `SearchEngine.remove_bid`
    deletes the bid's points, its BM25 corpus and its manifest entry;
    `services.remove_bid` then deletes `outputs/<id>*`, `outputs/parsed/<id>/`
    and the data folder, refuses while an extraction of that bid is running
    (409), and is exposed as `DELETE /bids/{id}`, `python main.py remove <id>`
    and a "Remove bid" button on the bid page. The smoke test uploads a
    throwaway copy of the first bid and removes it again.
103. **Addendum notes are de-duplicated.** On a retry the reconciler
    re-applied the same clarification and the extractor often echoed the
    decision text in its own notes, so records carried the same sentence
    two or three times ("Clarified by Addendum 1: X. X"). `_join_notes` now
    keeps the more complete of the two texts when one contains the other
    and strips the trailing period of the decision text before it is wrapped.

104. **`llm.max_tokens` 4000 -> 12000, `timeout_s` 60 -> 90, truncation is
    named.** With 12 evidence passages for the documentation field (item 99)
    the commercial/legal extractor's prompt grew to about 30k tokens and the
    reasoning model spent the whole 4000-token output budget on hidden
    reasoning: the reply came back empty, was retried with a "not valid for
    the schema" hint (useless for an empty reply), failed again, and the
    nine fields of that group went through two retry rounds before a smaller
    retry prompt fitted. The providers now report when a reply stopped at the
    limit (`finish_reason == "length"` / `stop_reason == "max_tokens"`),
    `llm.structured` retries such a reply once with the unchanged prompt and
    otherwise raises `LLMTruncated` whose message names the budget, and the
    budget itself is raised so a group prompt of that size completes in one
    call. Output tokens are paid for only when produced, so the higher cap
    does not change the cost of ordinary calls.
105. **An item is dropped only when it is also ungrounded.** The first run
    with item 100's correction removed items that were right: Bid2's RAM,
    display and OS specification lines (all verbatim on the spec sheet), the
    attached Contract Affidavit, and six Bid1 product quantities, because the
    critic's "unsupported" verdict is an LLM judgement and was taken as
    final. `drop_unsupported_items` now receives the texts of every passage
    retrieved for the field and keeps a named item whose content words
    (three or more characters, function words ignored, at least 60 %)
    occur in them; only an item the critic rejects *and* the evidence does
    not contain is removed. An item with no evidence texts at hand (unit
    tests, direct calls) is judged by the verdict alone as before. Two field
    descriptions were also tightened after that run: Part_no excludes the
    line numbers of a price sheet, and Payment Terms excludes clauses about a
    vendor paying its own subcontractors.

106. **Removing a bid tolerates Windows file locks and a busy index.** A
    freshly uploaded PDF can be held open for a few seconds by a virus
    scanner, the search indexer or a PDF viewer; deleting it then raises
    `PermissionError` (WinError 32), which previously surfaced as a raw 500
    and left the bid half-removed. `remove_bid` now retries a locked delete
    for up to 6 s (0.5 s interval) and, if the lock persists, raises a
    RuntimeError naming the path, which the API answers as 409 so the UI
    shows "in use, try again" instead of a stack trace. Likewise
    `SearchEngine.remove_bid` no longer waits indefinitely behind the write
    lock that `index_bid` holds for a whole indexing run: it gives up after
    5 s with "the index is busy", instead of hanging the DELETE request for
    minutes. Both timeouts are module constants, not config keys, because no
    user would tune them.
107. **Remove stops the bid's runs; Stop acts within one call.** Cancellation
    was only checked between graph nodes, so "Stop" could wait minutes for a
    retrieval or LLM node to finish, and DELETE /bids/{id} flatly refused (409)
    while anything ran. Now: (a) `llm.py` checks the run's cancel flag before
    every provider request (via the step recorder, which now carries the
    run_id), and every node re-raises `RunCancelled` instead of swallowing it
    as a field error; (b) `SearchEngine.index_bid` takes an optional
    `should_stop` callback, polled between parsing and each 64-text embedding
    batch, always before the stores are mutated, so a cancelled index changes
    nothing; (c) `remove_bid` cancels the bid's extraction and upload-indexing
    runs and waits up to 15 s for them to unwind before deleting, answering
    409 "still stopping" past that, which the web app retries automatically
    (3 s interval, up to 20 times) while the button shows "Removing...". An
    upload whose indexing was cancelled no longer auto-starts its extraction.
