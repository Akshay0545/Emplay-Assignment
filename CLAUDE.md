# Project: RFP Intelligence Platform

Source of truth: `docs/BUILD_PLAN.md` (requirements, stack, layout, schemas,
module specs, phases). Assignment brief: `docs/Assignment-AI_Engineer.pdf`.
Bid inputs: `data/Bid1/`, `data/Bid2/`.

## How we work
- Work ONE phase at a time. When I say "Do Phase N", read that phase in
  docs/BUILD_PLAN.md, show me the file list and plan, and wait for "go".
- End every phase by running its acceptance checks (pytest, commands,
  frontend checks) and showing me the real output. Do not commit or start
  the next phase until I approve.
- If a check fails twice, stop and explain the cause and a proposed fix.
  Never weaken or delete a test to make it pass.
- Record any decision the plan does not cover in docs/ASSUMPTIONS.md.

## Hard rules
- Follow BUILD_PLAN.md layout, schemas, function names and config keys exactly.
- Never hard-code anything specific to Bid1/Bid2 (names, IDs, dates, pages)
  in source code. Bid-specific facts live only in eval/ and test fixtures.
- Settings live in config/*.yaml; secrets only in .env. Never print keys.
- Every LLM call goes through rfp_intel/llm.py and returns a Pydantic model.
- Every agent-to-agent message is a Pydantic model from rfp_intel/schemas.py.
- Only the Retrieval agent (node `retrieve`) calls the search engine.
- Missing info = value null, status "not_found", note "Not found in documents".
  Every non-null value has at least one citation.
- One bad file, LLM timeout, bad JSON or empty retrieval must never crash a run.
- Python: type hints + docstrings, small functions. TypeScript: strict mode,
  small typed components. Readable code over clever code.

## Commands
- Python env: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
- Tests: pytest -q
- One-command run: python main.py --bid ./data/Bid1
- Search: python main.py search "<query>" --bid Bid1 [--type addendum] [--config hybrid_rerank]
- Ask: python main.py ask "<question>"
- Eval: python main.py eval retrieval | python main.py eval extraction
- API: uvicorn rfp_intel.api.server:app --reload   (http://localhost:8000/docs)
- Web app: cd frontend && npm install && npm run dev   (http://localhost:5173)
- Frontend checks: cd frontend && npm run lint && npm test && npm run build
