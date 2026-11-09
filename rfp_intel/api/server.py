"""FastAPI app exposing index, search, extract, ask and bid endpoints (Phase 7a).

Handlers are thin: every endpoint calls one function in rfp_intel.services.
The shared search engine is opened once in the lifespan and closed on
shutdown. Every error body has the shape {"error": str, "run_id": null}.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from rfp_intel import llm, services
from rfp_intel.agents.runtime import close_engine, get_engine
from rfp_intel.config import settings
from rfp_intel.schemas import (
    Answer,
    BidDetail,
    RemoveResult,
    BidRecord,
    BidSummary,
    Chunk,
    ComparisonReport,
    GoNoGoReport,
    IndexStarted,
    IndexStats,
    RunInfo,
    RunStatus,
    SearchHit,
)

_MEDIA_TYPES = {".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html"}


class IndexRequest(BaseModel):
    """POST /index body: a folder path or a bid id under data_dir."""

    folder: str
    force: bool = False


class ExtractRequest(BaseModel):
    """POST /extract body."""

    bid_id: str


class AskRequest(BaseModel):
    """POST /ask and POST /ask/runs body."""

    question: str
    bid_ids: list[str] | None = None

    @field_validator("question")
    @classmethod
    def _question_length(cls, value: str) -> str:
        """Non-empty and at most api.max_question_chars characters."""
        text = value.strip()
        limit = settings.api.max_question_chars
        if not text:
            raise ValueError("question must not be empty")
        if len(text) > limit:
            raise ValueError(f"question is {len(text)} characters; the limit is {limit}")
        return text


class CompareRequest(BaseModel):
    """POST /compare body: two or more bid ids with saved records."""

    bid_ids: list[str]


class SearchResponse(BaseModel):
    """GET /search body."""

    hits: list[SearchHit]


class RunStarted(BaseModel):
    """POST /extract and POST /ask/runs response."""

    run_id: str


class AskQueue(BaseModel):
    """Background question load (GET /health)."""

    running: int
    queued: int
    workers: int


class RunMetrics(BaseModel):
    """Per-kind run counts and latency percentiles (GET /metrics)."""

    running: int
    done: int
    failed: int
    p50_ms: int | None
    p95_ms: int | None


class CacheMetrics(BaseModel):
    """Answer cache counters (GET /metrics)."""

    enabled: bool
    hits: int
    misses: int


class Metrics(BaseModel):
    """GET /metrics response: what this API process has done since it started."""

    runs: dict[str, RunMetrics]
    ask_queue: AskQueue
    cache: CacheMetrics


class Health(BaseModel):
    """GET /health response."""

    status: str
    indexed_bids: list[str]
    ask_queue: AskQueue
    llm_provider: str                    # provider calls go to (the fallback once it kicked in)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Open and warm the shared engine on startup (keeps one already injected); close it on shutdown."""
    try:
        get_engine().warm_up()
    except RuntimeError as exc:
        if "already accessed by another instance" in str(exc):
            raise RuntimeError(
                "the embedded Qdrant store is held by another process; run one API process with "
                "vector_store.kind: qdrant_local, or set vector_store.kind: qdrant_server (QDRANT_URL) "
                "to run several"
            ) from exc
        raise
    try:
        yield
    finally:
        services.shutdown_background()
        close_engine()


app = FastAPI(title="RFP Intelligence API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.api.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error(status: int, message: str) -> JSONResponse:
    """The documented error body."""
    return JSONResponse(status_code=status, content={"error": message, "run_id": None})


@app.exception_handler(StarletteHTTPException)
async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTP errors as {error, run_id}."""
    return _error(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Bad request bodies / query params as {error, run_id}."""
    return _error(422, "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()))


@app.exception_handler(Exception)
async def _unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
    """Any other failure as {error, run_id} with status 500."""
    return _error(500, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- endpoints

@app.get("/health", response_model=Health)
def health() -> Health:
    """Liveness, the bids the engine knows and the background question load."""
    return Health(status="ok", indexed_bids=list(get_engine().known_bids()),
                  ask_queue=AskQueue(**services.ask_queue_stats()), llm_provider=llm.active_provider())


@app.get("/metrics", response_model=Metrics)
def metrics() -> Metrics:
    """Run counts, latency percentiles, question queue and answer-cache counters for this process."""
    return Metrics(**services.metrics())


@app.get("/bids", response_model=list[BidSummary])
def bids() -> list[BidSummary]:
    """Every bid folder under data_dir with headline facts."""
    return services.list_bids()


@app.post("/bids", response_model=IndexStarted, status_code=202)
def upload_bid(bid_id: str = Form(...), files: list[UploadFile] = File(...)) -> IndexStarted:
    """Save uploaded files into data/<bid_id> and start indexing in the background.

    Returns as soon as the bytes are on disk. Parsing and embedding a large PDF
    takes minutes, so it runs in a worker thread: the bid is listed immediately
    with `indexing=True` and the caller can poll `GET /bids` or `GET /runs/{id}`
    instead of holding the request open.
    """
    payload = [(upload.filename or "", upload.file.read()) for upload in files]
    try:
        folder = services.save_upload(bid_id, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    run_id = services.start_indexing(folder)
    return IndexStarted(bid_id=folder.name, run_id=run_id, documents=len(payload))


@app.post("/index", response_model=IndexStats)
def index(body: IndexRequest) -> IndexStats:
    """Index a folder (a path or a bid id under data_dir)."""
    folder = Path(body.folder)
    if not folder.is_dir():
        folder = services.bid_folder(body.folder)
    if not folder.is_dir():
        raise HTTPException(404, f"folder not found: {body.folder}")
    return services.index_bid(folder, force=body.force)


@app.get("/bids/{bid_id}", response_model=BidDetail)
def bid_detail(bid_id: str) -> BidDetail:
    """Documents and index state of one bid."""
    try:
        return services.get_bid_detail(bid_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/bids/{bid_id}", response_model=RemoveResult)
def remove_bid(bid_id: str) -> RemoveResult:
    """Remove a bid from the index and delete its outputs and data folder."""
    try:
        return services.remove_bid(bid_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/bids/{bid_id}/record", response_model=BidRecord)
def bid_record(bid_id: str) -> BidRecord:
    """Latest saved BidRecord of a bid."""
    record = services.get_record(bid_id)
    if record is None:
        raise HTTPException(404, f"no record for bid {bid_id!r}")
    return record


@app.post("/extract", response_model=RunStarted, status_code=202)
def extract(body: ExtractRequest) -> RunStarted:
    """Start a background extraction run."""
    try:
        return RunStarted(run_id=services.start_extraction(body.bid_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except services.ExtractionBusy as exc:
        # The body's run_id points at the run that holds the lock so a client can follow it.
        active = exc.active.run_id if exc.active else None
        return JSONResponse(status_code=409, content={"error": str(exc), "run_id": active})


@app.get("/runs", response_model=list[RunInfo])
def runs() -> list[RunInfo]:
    """Extractions and questions started by this API process, newest first."""
    return services.list_runs()


@app.get("/runs/{run_id}", response_model=RunStatus)
def run(run_id: str) -> RunStatus:
    """Status and trace steps of a run; a finished question also carries its answer."""
    try:
        return services.run_status(run_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown run {run_id!r}") from exc


@app.post("/runs/{run_id}/cancel", response_model=RunInfo)
def cancel_run(run_id: str) -> RunInfo:
    """Ask a running run to stop.

    The run is a thread, so it stops at its next node boundary rather than
    instantly: the node in flight finishes, then the graph unwinds and the run
    is reported as `cancelled`. 404 when this process does not know the run,
    409 when it has already finished.
    """
    try:
        return services.cancel_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, f"unknown run {run_id!r}") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/search", response_model=SearchResponse)
def search(q: str, bid_id: str | None = None, doc_type: str | None = None, addendum: int | None = None,
           top_k: int | None = None, config: str | None = None) -> SearchResponse:
    """Hybrid search with optional filters and a named retrieval config."""
    try:
        hits = services.search(q, bid_id=bid_id, doc_type=doc_type, addendum=addendum, top_k=top_k, config=config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return SearchResponse(hits=hits)


@app.post("/ask", response_model=Answer)
def ask(body: AskRequest) -> Answer:
    """Answer a question with citations (blocks until the answer is ready)."""
    return services.ask(body.question, body.bid_ids)


@app.post("/ask/runs", response_model=RunStarted, status_code=202)
def ask_run(body: AskRequest) -> RunStarted:
    """Start answering a question in the background; poll GET /runs/{run_id} for steps and the answer."""
    try:
        return RunStarted(run_id=services.start_question(body.question, body.bid_ids))
    except services.AskQueueFull as exc:
        raise HTTPException(429, str(exc)) from exc


@app.get("/chunks/{chunk_id}", response_model=Chunk)
def chunk(chunk_id: str) -> Chunk:
    """One indexed chunk by id."""
    found = services.get_chunk(chunk_id)
    if found is None:
        raise HTTPException(404, f"unknown chunk {chunk_id!r}")
    return found


@app.get("/files/{bid_id}/{file_name:path}")
def file(bid_id: str, file_name: str) -> FileResponse:
    """Serve an original input file; path traversal is rejected."""
    try:
        path = services.safe_file_path(bid_id, file_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, f"file not found: {file_name}")
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@app.post("/compare", response_model=ComparisonReport)
def compare(body: CompareRequest) -> ComparisonReport:
    """Run the Comparison agent over the bids' saved records (bonus); also saved as outputs/comparison.*."""
    try:
        return services.compare_bids(body.bid_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/compare", response_model=ComparisonReport)
def latest_comparison() -> ComparisonReport:
    """The last comparison report written by POST /compare or the CLI."""
    report = services.latest_comparison()
    if report is None:
        raise HTTPException(404, "no comparison yet; POST /compare or run: python main.py compare Bid1 Bid2")
    return report


@app.post("/bids/{bid_id}/go-no-go", response_model=GoNoGoReport)
def assess_go_no_go(bid_id: str) -> GoNoGoReport:
    """Run the Go/No-Go agent for a bid against config/capabilities.yaml (bonus)."""
    try:
        return services.assess_go_no_go(bid_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/bids/{bid_id}/go-no-go", response_model=GoNoGoReport)
def go_no_go(bid_id: str) -> GoNoGoReport:
    """The saved go / no-go report of a bid."""
    report = services.get_go_no_go(bid_id)
    if report is None:
        raise HTTPException(404, f"no go/no-go report for bid {bid_id!r}; POST /bids/{bid_id}/go-no-go")
    return report


@app.get("/eval/retrieval")
def eval_retrieval() -> dict:
    """Latest retrieval evaluation results."""
    return _eval("retrieval")


@app.get("/eval/extraction")
def eval_extraction() -> dict:
    """Latest extraction evaluation results."""
    return _eval("extraction")


def _eval(kind: str) -> dict:
    """Eval results of one kind or 404."""
    results = services.eval_results(kind)  # type: ignore[arg-type]
    if results is None:
        raise HTTPException(404, f"no {kind} eval results yet; run: python main.py eval {kind}")
    return results
