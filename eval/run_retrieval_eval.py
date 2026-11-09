"""Retrieval evaluation: Recall@k and MRR per retrieval config (BUILD_PLAN Phase 4).

For every question x config, search with the question's bid filter and
top_k = max(recall_k); a hit is relevant when it comes from the expected
file and contains the key phrase. Writes eval/results/retrieval.md and
retrieval.json.

    python -m eval.run_retrieval_eval          # or: python main.py eval retrieval
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from rfp_intel.config import settings
from rfp_intel.schemas import SearchFilters, SearchHit

from eval.loaders import EVAL_DIR, RetrievalQuestion, is_relevant, load_retrieval_questions

RESULTS_DIR = EVAL_DIR / "results"
DEFAULT_CONFIGS = ("vector", "hybrid", "hybrid_rerank")


def first_relevant_rank(hits: list[SearchHit], question: RetrievalQuestion) -> int | None:
    """1-based rank of the first relevant hit, or None."""
    for rank, hit in enumerate(hits, start=1):
        if is_relevant(hit.chunk.file_name, hit.chunk.text, question):
            return rank
    return None


def summarize(ranks: list[int | None], recall_k: list[int]) -> dict[str, float]:
    """Recall@k for each k, MRR and nDCG@max(k) over a list of first-relevant ranks.

    nDCG uses binary relevance with one expected passage per question, so the
    ideal DCG is 1 and nDCG@k = 1 / log2(rank + 1) when rank <= k, else 0.
    """
    n = len(ranks) or 1
    k_max = max(recall_k)
    metrics = {f"recall@{k}": sum(1 for r in ranks if r is not None and r <= k) / n for k in recall_k}
    metrics["mrr"] = sum(1 / r for r in ranks if r is not None) / n
    metrics[f"ndcg@{k_max}"] = sum(1 / math.log2(r + 1) for r in ranks if r is not None and r <= k_max) / n
    return {name: round(value, 4) for name, value in metrics.items()}


def run_retrieval_eval(engine=None, configs: tuple[str, ...] = DEFAULT_CONFIGS,
                       questions: list[RetrievalQuestion] | None = None,
                       results_dir: Path = RESULTS_DIR) -> dict:
    """Evaluate every config and write the markdown + JSON reports."""
    from rfp_intel.search.engine import SearchEngine

    own_engine = engine is None
    engine = engine or SearchEngine()
    questions = questions if questions is not None else load_retrieval_questions()
    recall_k = settings.eval.recall_k
    top_k = max(recall_k)
    try:
        per_question: list[dict] = []
        for q in questions:
            row = {"id": q.id, "question": q.question, "bid_id": q.bid_id,
                   "expected_file": q.expected_file, "key_phrase": q.key_phrase, "ranks": {}}
            for config in configs:
                hits = engine.search(q.question, SearchFilters(bid_id=q.bid_id), top_k=top_k, config=config)
                row["ranks"][config] = first_relevant_rank(hits, q)
            per_question.append(row)
    finally:
        if own_engine:
            engine.close()

    summary = {config: summarize([row["ranks"][config] for row in per_question], recall_k)
               for config in configs}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "top_k": top_k,
        "configs": {name: settings.retrieval_for(name).model_dump() for name in configs},
        "summary": summary,
        "questions": per_question,
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "retrieval.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (results_dir / "retrieval.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def render_markdown(report: dict) -> str:
    """Markdown report: question set, summary table, per-question ranks."""
    configs = list(report["summary"])
    metrics = list(next(iter(report["summary"].values())))
    lines = [
        "# Retrieval evaluation",
        "",
        f"Generated {report['generated_at']}; top_k={report['top_k']}; "
        f"{len(report['questions'])} questions. A hit is relevant if it comes from the expected "
        "file and contains the key phrase (case/whitespace-insensitive).",
        "",
        "## Summary",
        "",
        "| config | " + " | ".join(metrics) + " |",
        "|---|" + "---|" * len(metrics),
    ]
    for config in configs:
        values = report["summary"][config]
        lines.append(f"| {config} | " + " | ".join(f"{values[m]:.3f}" for m in metrics) + " |")
    lines += ["", "Configs: " + "; ".join(
        f"{name}: bm25={cfg['use_bm25']}, reranker={cfg['use_reranker']}"
        for name, cfg in report["configs"].items()), "", "## Per-question first relevant rank", "",
        "| # | question | bid | expected file | key phrase | " + " | ".join(configs) + " |",
        "|---|---|---|---|---|" + "---|" * len(configs)]
    for row in report["questions"]:
        ranks = " | ".join("miss" if row["ranks"][c] is None else str(row["ranks"][c]) for c in configs)
        lines.append(f"| {row['id']} | {row['question']} | {row['bid_id']} | {row['expected_file']} | "
                     f"{row['key_phrase']} | {ranks} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    """CLI entry point: run and print the summary."""
    report = run_retrieval_eval()
    for config, values in report["summary"].items():
        print(config, values)
    print(f"written: {RESULTS_DIR / 'retrieval.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
