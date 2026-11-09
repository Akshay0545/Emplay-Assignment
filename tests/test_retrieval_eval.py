"""Tests for eval/run_retrieval_eval.py with a fake engine."""

import math

from eval.loaders import RetrievalQuestion
from eval.run_retrieval_eval import first_relevant_rank, render_markdown, run_retrieval_eval, summarize
from rfp_intel.schemas import Chunk, DocType, SearchHit


def _hit(file_name: str, text: str) -> SearchHit:
    chunk = Chunk(chunk_id="c", bid_id="B", file_name=file_name, doc_type=DocType.RFP, addendum_number=None,
                  page=1, doc_date=None, section=None, kind="text", header="", text=text, token_count=1)
    return SearchHit(chunk=chunk, score=1.0)


Q = RetrievalQuestion(id=1, question="due?", bid_id="B", expected_file="a.pdf", key_phrase="July 9, 2024")


def test_first_relevant_rank_needs_file_and_phrase():
    hits = [_hit("b.pdf", "July 9, 2024"), _hit("a.pdf", "nothing"), _hit("a.pdf", "due July  9, 2024")]
    assert first_relevant_rank(hits, Q) == 3
    assert first_relevant_rank(hits[:2], Q) is None


def test_summarize_metrics():
    ndcg3 = round((1 + 1 / math.log2(4) + 1 / math.log2(3)) / 4, 4)
    assert summarize([1, 3, None, 2], [1, 3]) == {"recall@1": 0.25, "recall@3": 0.75,
                                                  "mrr": round((1 + 1/3 + 0.5) / 4, 4), "ndcg@3": ndcg3}
    assert summarize([], [1]) == {"recall@1": 0.0, "mrr": 0.0, "ndcg@1": 0.0}
    assert summarize([5], [3])["ndcg@3"] == 0.0          # beyond the cutoff


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.closed = False

    def search(self, query, filters, top_k, config):
        self.calls.append((query, filters.bid_id, top_k, config))
        return [_hit("a.pdf", "July 9, 2024")] if config != "vector" else [_hit("a.pdf", "other")]

    def close(self):
        self.closed = True


def test_run_writes_reports_and_filters_by_bid(tmp_path):
    engine = FakeEngine()
    report = run_retrieval_eval(engine=engine, configs=("vector", "hybrid"), questions=[Q], results_dir=tmp_path)
    assert {c[1] for c in engine.calls} == {"B"} and {c[3] for c in engine.calls} == {"vector", "hybrid"}
    assert report["summary"]["hybrid"]["mrr"] == 1.0 and report["summary"]["vector"]["mrr"] == 0.0
    assert (tmp_path / "retrieval.md").exists() and (tmp_path / "retrieval.json").exists()
    assert not engine.closed                     # a caller-provided engine stays open
    md = render_markdown(report)
    assert "| hybrid | " in md and "| miss |" in md or "miss" in md
