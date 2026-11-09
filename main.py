"""RFP Intelligence Platform CLI.

    python main.py --bid ./data/Bid1              index + extract + results table
    python main.py --bid ./data/Bid1 --ask "..."  answer one question
    python main.py parse|index|search|extract|ask|run|compare|go-no-go ...
    python main.py eval retrieval|extraction
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from rfp_intel.config import settings

logging.basicConfig(level=logging.WARNING, format="%(message)s", handlers=[RichHandler(show_path=False)])
for _noisy in ("sentence_transformers", "transformers", "huggingface_hub", "httpx"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

app = typer.Typer(
    help="RFP Intelligence Platform: parse, index, search, extract and ask over bid folders.",
    no_args_is_help=False,
    add_completion=False,
)
eval_app = typer.Typer(help="Run evaluations and write eval/results/.")
app.add_typer(eval_app, name="eval")
cache_app = typer.Typer(help="Semantic answer cache (config: cache.enabled, cache.similarity_threshold).")
app.add_typer(cache_app, name="cache")

console = Console()


@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    bid: Optional[str] = typer.Option(None, "--bid", help="Bid folder, e.g. ./data/Bid1"),
    ask: Optional[str] = typer.Option(None, "--ask", help="Answer one question about the bid"),
) -> None:
    """Default: index + extract the bid and print a results table (or --ask a question)."""
    if ctx.invoked_subcommand is not None:
        return
    if bid is None:
        console.print(ctx.get_help())
        raise typer.Exit(code=0)
    folder = _bid_folder(bid)
    if ask:
        _ensure_indexed(folder)
        _answer(ask, [folder.name])
    else:
        _extract_with_graph(folder)


def _bid_folder(bid: str) -> Path:
    """Resolve --bid: an existing folder path, or a bid id under data_dir."""
    path = Path(bid)
    if path.is_dir():
        return path
    candidate = settings.path("data_dir") / bid
    if candidate.is_dir():
        return candidate
    console.print(f"[red]Bid folder not found: {bid}[/red]")
    raise typer.Exit(code=2)


@app.command()
def parse(bid: str = typer.Option(..., "--bid", help="Bid folder or id")) -> None:
    """Parse a bid folder and write outputs/parsed/<bid>/<file>.json."""
    from rfp_intel.ingestion.loader import load_bid_folder

    folder = _bid_folder(bid)
    documents = load_bid_folder(folder)
    out_dir = settings.path("outputs_dir") / "parsed" / folder.name
    out_dir.mkdir(parents=True, exist_ok=True)
    for doc in documents:
        (out_dir / f"{doc.file_name}.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")

    table = Table(title=f"Parsed {folder.name} -> {out_dir}")
    for column in ("file", "doc_type", "addendum", "pages", "tables", "warnings"):
        table.add_column(column)
    for doc in documents:
        table.add_row(
            doc.file_name,
            doc.doc_type.value,
            str(doc.addendum_number or ""),
            str(len(doc.pages)),
            str(sum(len(page.tables) for page in doc.pages)),
            "; ".join(doc.warnings),
        )
    parsed = {doc.file_name for doc in documents}
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.name not in parsed):
        table.add_row(path.name, "skipped", "", "", "", "unsupported type or duplicate")
    console.print(table)


@app.command()
def index(
    bid: str = typer.Option(..., "--bid", help="Bid folder or id"),
    force: bool = typer.Option(False, "--force", help="Re-index even if unchanged"),
) -> None:
    """Index a bid folder into the hybrid search engine (incremental)."""
    from rfp_intel.agents.runtime import close_engine, get_engine

    try:
        stats = get_engine().index_bid(_bid_folder(bid), force=force)
    finally:
        close_engine()
    state = "unchanged, skipped" if stats.skipped else "indexed"
    console.print(
        f"[bold]{stats.bid_id}[/bold]: {state} | documents={stats.documents} "
        f"chunks_added={stats.chunks_added} chunks_total={stats.chunks_total} ({stats.seconds}s)"
    )
    for warning in stats.warnings:
        console.print(f"  [yellow]warning:[/yellow] {warning}")


@app.command()
def remove(bid: str = typer.Argument(..., help="Bid id to delete from the index, outputs/ and data/")) -> None:
    """Remove a bid everywhere (index, saved record and summaries, parsed files, data folder)."""
    from rfp_intel import services
    from rfp_intel.agents.runtime import close_engine

    try:
        result = services.remove_bid(Path(bid).name)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    finally:
        close_engine()
    console.print(f"[bold]{result.bid_id}[/bold]: removed | indexed={result.indexed} "
                  f"outputs_removed={result.outputs_removed} folder_removed={result.folder_removed}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    bid: Optional[str] = typer.Option(None, "--bid", help="Bid id filter, e.g. Bid1"),
    doc_type: Optional[str] = typer.Option(None, "--type", help="Doc type filter, e.g. addendum"),
    addendum: Optional[int] = typer.Option(None, "--addendum", help="Addendum number filter"),
    top_k: Optional[int] = typer.Option(None, "--top-k", help="Number of hits"),
    config: Optional[str] = typer.Option(None, "--config", help="vector | hybrid | hybrid_rerank"),
) -> None:
    """Search indexed bids and print ranked hits with citations."""
    from rfp_intel.agents.runtime import close_engine, get_engine
    from rfp_intel.schemas import DocType, SearchFilters

    try:
        doc_types = [DocType(doc_type)] if doc_type else None
        settings.retrieval_for(config)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    filters = SearchFilters(bid_id=Path(bid).name if bid else None, doc_types=doc_types,
                            addendum_number=addendum)
    try:
        hits = get_engine().search(query, filters, top_k=top_k, config=config)
    finally:
        close_engine()

    table = Table(title=f"{query!r} ({config or 'default'})", show_lines=True)
    for column in ("rank", "score", "file", "page", "doc_type", "ranks", "text"):
        table.add_column(column)
    for rank, hit in enumerate(hits, start=1):
        c = hit.chunk
        kind = c.doc_type.value + (f" {c.addendum_number}" if c.addendum_number else "")
        table.add_row(str(rank), f"{hit.score:.3f}", c.file_name, str(c.page), kind,
                      " ".join(f"{k}={v}" for k, v in hit.ranks.items()),
                      c.text[:200].replace("\n", " "))
    console.print(table if hits else "[yellow]No results.[/yellow]")


def _ensure_indexed(folder: Path) -> None:
    """Index a bid folder with the shared engine (no-op when unchanged)."""
    from rfp_intel.agents.runtime import get_engine

    stats = get_engine().index_bid(folder)
    console.print(f"[bold]{stats.bid_id}[/bold]: {'index unchanged' if stats.skipped else 'indexed'} "
                  f"({stats.chunks_total} chunks)")


def _extract_with_graph(folder: Path) -> None:
    """Index, run the multi-agent extraction graph and print the results table."""
    from rfp_intel.agents.graph import run_extraction
    from rfp_intel.agents.runtime import close_engine
    from rfp_intel.logging_utils import trace_path

    try:
        _ensure_indexed(folder)
        state = run_extraction(folder.name)
    finally:
        close_engine()
    record = state["record"]
    _print_record(record)
    steps = state.get("usage", [])
    cost = sum(s.cost_usd for s in steps)
    tokens = sum(s.tokens_in + s.tokens_out for s in steps)
    console.print(f"steps={len(steps)} tokens={tokens} cost=${cost:.4f} trace={trace_path(record.run_id)}")
    for error in state.get("errors", []):
        console.print(f"  [yellow]{error}[/yellow]")


def _answer(question: str, bids: list[str] | None) -> None:
    """Answer a question (answer cache first, then the Q&A graph) and print it with its citations."""
    from rfp_intel import services
    from rfp_intel.agents.runtime import close_engine

    try:
        answer = services.ask(question, bids)
    finally:
        close_engine()
    console.print(f"[bold]Q:[/bold] {answer.question}")
    console.print(f"[bold]A:[/bold] {answer.answer}")
    console.print(f"confidence={answer.confidence:.2f} bids={', '.join(answer.bids)}"
                  + (" [dim](cached)[/dim]" if answer.cached else ""))
    for n, c in enumerate(answer.citations, start=1):
        console.print(f"  [{n}] {c.file} p.{c.page}: {c.quote[:160]!r}")


def _print_record(record) -> None:
    """The results table for a BidRecord."""
    from rfp_intel.agents.baseline import value_text

    table = Table(title=f"{record.bid_id} -> outputs/{record.bid_id}.json (run {record.run_id})")
    for column in ("field", "status", "confidence", "value", "#citations"):
        table.add_column(column)
    for name, field in record.fields.items():
        text = value_text(field.value).replace("\n", " ")
        table.add_row(name, field.status, f"{field.confidence:.2f}",
                      text if len(text) <= 70 else text[:67] + "...", str(len(field.sources)))
    console.print(table)
    console.print(f"validation: {record.validation}")
    for change in record.addendum_changes:
        console.print(f"  addendum change: {change.field} ({change.kind}, Addendum {change.addendum_number}): "
                      f"{change.old_value or 'not stated'} -> {change.new_value}")


@app.command()
def extract(
    bid: str = typer.Option(..., "--bid", help="Bid folder or id"),
    baseline: bool = typer.Option(False, "--baseline", help="Run the single-pass baseline extractor instead"),
) -> None:
    """Index the bid, run the extraction graph (or the baseline) and write outputs/<bid>.json."""
    folder = _bid_folder(bid)
    if not baseline:
        _extract_with_graph(folder)
        return
    from rfp_intel.agents.baseline import run_baseline
    from rfp_intel.agents.runtime import close_engine, get_engine

    try:
        _ensure_indexed(folder)
        record = run_baseline(folder.name, get_engine())
    finally:
        close_engine()
    _print_record(record)


@app.command(name="ask")
def ask_command(
    question: str = typer.Argument(..., help="Question to answer"),
    bid: Optional[list[str]] = typer.Option(None, "--bid", help="Bid id(s) to scope the question"),
) -> None:
    """Answer a free-form question with citations (all indexed bids unless --bid is given)."""
    _answer(question, [Path(b).name for b in bid] if bid else None)


@app.command()
def run(bid: str = typer.Option(..., "--bid", help="Bid folder or id")) -> None:
    """Index (if needed) and extract one bid end to end (same as the default --bid)."""
    _extract_with_graph(_bid_folder(bid))


@app.command()
def compare(
    bids: list[str] = typer.Argument(..., help="Two or more bid ids with saved records, e.g. Bid1 Bid2"),
) -> None:
    """Compare bids side by side from their saved records -> outputs/comparison.md (bonus)."""
    from rfp_intel import services
    from rfp_intel.agents.runtime import close_engine

    ids = [Path(b).name for b in bids]
    try:
        report = services.compare_bids(ids)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        close_engine()
    console.print(report.narrative)
    table = Table(title=f"{' vs '.join(report.bid_ids)} -> outputs/comparison.md (run {report.run_id})",
                  show_lines=True)
    for column in ["field", *report.bid_ids, "differs"]:
        table.add_column(column)
    for row in report.rows:
        table.add_row(row.field, *[_cell_text(c.value) for c in row.cells], "yes" if row.differs else "")
    console.print(table)
    for item in report.highlights:
        console.print(f"  [bold]{item.field}[/bold]: {item.note}")


def _cell_text(value, width: int = 60) -> str:
    """A comparison cell for the terminal table."""
    from rfp_intel.agents.baseline import value_text

    text = value_text(value).replace("\n", " ") if value is not None else "Not found in documents"
    return text if len(text) <= width else text[: width - 3] + "..."


@app.command(name="go-no-go")
def go_no_go_command(bid: str = typer.Option(..., "--bid", help="Bid folder or id (extracted already)")) -> None:
    """Go / no-go recommendation against config/capabilities.yaml -> outputs/<bid>_go_no_go.md (bonus)."""
    from rfp_intel import services
    from rfp_intel.agents.nodes.go_no_go import LABELS
    from rfp_intel.agents.runtime import close_engine

    bid_id = _bid_folder(bid).name
    try:
        report = services.assess_go_no_go(bid_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        close_engine()
    colour = {"go": "green", "no_go": "red", "review": "yellow"}[report.recommendation]
    console.print(f"[bold {colour}]{LABELS[report.recommendation]}[/bold {colour}] for {bid_id}: {report.summary}")
    table = Table(title=f"outputs/{bid_id}_go_no_go.md (run {report.run_id})", show_lines=True)
    for column in ("criterion", "status", "critical", "reason", "#citations"):
        table.add_column(column)
    for item in report.criteria:
        table.add_row(item.label, item.status.replace("_", " "), "yes" if item.critical else "",
                      item.reason, str(len(item.sources)))
    console.print(table)


@cache_app.command("clear")
def cache_clear() -> None:
    """Delete every cached answer."""
    from rfp_intel import services
    from rfp_intel.agents.runtime import close_engine

    try:
        removed = services.clear_answer_cache()
    finally:
        close_engine()
    console.print(f"removed {removed} cached answer(s)")


@cache_app.command("stats")
def cache_stats() -> None:
    """How many answers are cached."""
    from rfp_intel.agents.runtime import close_engine, get_engine

    try:
        count = get_engine().answer_cache.count()
    finally:
        close_engine()
    console.print(f"cache enabled={settings.cache.enabled} threshold={settings.cache.similarity_threshold} "
                  f"answers={count}")


@eval_app.command("retrieval")
def eval_retrieval(
    min_recall5: Optional[float] = typer.Option(None, "--min-recall5",
                                               help="Exit 1 when the best config's recall@5 is below this (CI gate)"),
) -> None:
    """Recall@k and MRR for each retrieval config -> eval/results/retrieval.md."""
    from eval.run_retrieval_eval import RESULTS_DIR, run_retrieval_eval

    report = run_retrieval_eval()
    metrics = list(next(iter(report["summary"].values())))
    table = Table(title=f"Retrieval evaluation ({len(report['questions'])} questions, top_k={report['top_k']})")
    for column in ["config", *metrics]:
        table.add_column(column)
    for config, values in report["summary"].items():
        table.add_row(config, *[f"{values[m]:.3f}" for m in metrics])
    console.print(table)
    misses = [(q["id"], c) for q in report["questions"] for c, r in q["ranks"].items() if r is None]
    if misses:
        console.print("misses (question/config): " + ", ".join(f"{q}/{c}" for q, c in misses))
    console.print(f"wrote {RESULTS_DIR / 'retrieval.md'} and retrieval.json")
    if min_recall5 is not None:
        best = max(values.get("recall@5", 0.0) for values in report["summary"].values())
        if best < min_recall5:
            console.print(f"[red]best recall@5 {best:.3f} is below the floor {min_recall5:.3f}[/red]")
            raise typer.Exit(code=1)


@eval_app.command("extraction")
def eval_extraction(
    bid: Optional[list[str]] = typer.Option(None, "--bid", help="Bid id(s); default: all with gold"),
    no_judge: bool = typer.Option(False, "--no-judge", help="Skip the LLM judge"),
) -> None:
    """Field accuracy against gold answers -> eval/results/extraction.md."""
    from eval.run_extraction_eval import RESULTS_DIR, run_extraction_eval

    payload = run_extraction_eval(bid or None, judge=not no_judge)
    table = Table(title="Extraction accuracy (accuracy = (exact + 0.5*partial) / fields)")
    for column in ("bid", "fields", "exact", "partial", "wrong", "accuracy", "with citations"):
        table.add_column(column)
    for result in payload["bids"]:
        table.add_row(result["bid_id"], str(result["scored_fields"]), str(result["exact"]),
                      str(result["partial"]), str(result["wrong"]), f"{result['accuracy']:.3f}",
                      str(result["fields_with_citations"]))
    console.print(table)
    console.print(f"wrote {RESULTS_DIR / 'extraction.md'} and extraction.json")


if __name__ == "__main__":
    app()
