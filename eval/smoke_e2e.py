"""End-to-end smoke test of every mode against a running API and the real bids.

Unlike `pytest` (fakes, no network), this exercises the real search engine,
the real LLM and the real bid folders, and writes eval/results/smoke_e2e.md
with one PASS / FAIL row per check. It needs:

- the API running:  uvicorn rfp_intel.api.server:app --port 8001
- LLM_API_KEY etc. in .env (the API process reads them)
- the bids indexed and extracted at least once (python main.py --bid data/Bid1 ...)

    python -m eval.smoke_e2e                       # API at http://localhost:8001
    python -m eval.smoke_e2e --api http://localhost:8000 --bids Bid1 Bid2
    python -m eval.smoke_e2e --skip-llm            # only the checks that need no LLM call

CLI checks run the commands that do not open the embedded vector store
(parse, compare, go-no-go, --help), because the API process holds it; search,
ask, index and the evals are checked through the API. Exit code 1 when any
check fails.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results" / "smoke_e2e.md"
NOT_FOUND = "Not found in documents"


@dataclass
class Check:
    """One smoke check and its outcome."""

    name: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Smoke:
    """Runs the checks and collects their results."""

    api: str
    bids: list[str]
    skip_llm: bool
    checks: list[Check] = field(default_factory=list)

    # ------------------------------------------------------------ helpers

    def run(self, name: str, fn: Callable[[], str]) -> None:
        """Run one check; an exception is a failure, never a crash."""
        t0 = time.perf_counter()
        try:
            detail, ok = fn(), True
        except Exception as exc:  # noqa: BLE001 - report, do not stop
            detail, ok = f"{type(exc).__name__}: {str(exc)[:300]}", False
        self.checks.append(Check(name, ok, detail, round(time.perf_counter() - t0, 1)))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    def get(self, path: str, **params: Any) -> Any:
        """GET and raise on a non-2xx status."""
        response = httpx.get(f"{self.api}{path}", params=params or None, timeout=300)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: dict | None = None, expect: int = 200) -> Any:
        """POST JSON and require the expected status."""
        response = httpx.post(f"{self.api}{path}", json=body, timeout=600)
        if response.status_code != expect:
            raise AssertionError(f"{path} -> {response.status_code}: {response.text[:200]}")
        return response.json()

    def wait_run(self, run_id: str, timeout: float = 600) -> dict:
        """Poll GET /runs/{id} until the run leaves "running"."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = self.get(f"/runs/{run_id}")
            if body["status"] != "running":
                return body
            time.sleep(2)
        raise TimeoutError(f"run {run_id} still running after {timeout}s")

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        """Run `python main.py <args>` from the project root."""
        return subprocess.run([sys.executable, "main.py", *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)

    # ------------------------------------------------------------ checks

    def check_health(self) -> str:
        body = self.get("/health")
        missing = [b for b in self.bids if b not in body["indexed_bids"]]
        assert body["status"] == "ok" and not missing, f"not indexed: {missing}"
        return f"indexed {body['indexed_bids']}"

    def check_bids(self) -> str:
        rows = {r["bid_id"]: r for r in self.get("/bids")}
        for bid in self.bids:
            assert rows[bid]["indexed"] and rows[bid]["doc_count"] > 0, bid
        return ", ".join(f"{b}: {rows[b]['doc_count']} files" for b in self.bids)

    def check_bid_detail(self) -> str:
        parts = []
        for bid in self.bids:
            body = self.get(f"/bids/{bid}")
            assert body["indexed"] and body["chunks"] > 0 and body["documents"], bid
            parts.append(f"{bid}: {len(body['documents'])} docs, {body['chunks']} chunks")
        return "; ".join(parts)

    def check_index_incremental(self) -> str:
        parts = []
        for bid in self.bids:
            body = self.post("/index", {"folder": bid})
            assert body["chunks_added"] == 0 and body["skipped"], f"{bid} re-indexed: {body}"
            parts.append(f"{bid}: unchanged, {body['chunks_total']} chunks")
        return "; ".join(parts)

    def check_search_configs(self) -> str:
        bid = self.bids[0]
        parts = []
        for config in ("vector", "hybrid", "hybrid_rerank"):
            hits = self.get("/search", q="submission due date", bid_id=bid, top_k=5, config=config)["hits"]
            assert hits and all(h["chunk"]["bid_id"] == bid for h in hits), config
            assert all(h["chunk"]["file_name"] and h["chunk"]["page"] >= 1 and h["chunk"]["text"] for h in hits)
            parts.append(f"{config}: {len(hits)} hits")
        return "; ".join(parts)

    def check_search_filters(self) -> str:
        detail = self.get(f"/bids/{self.bids[0]}")
        types = {d["doc_type"] for d in detail["documents"]}
        doc_type = "addendum" if "addendum" in types else sorted(types)[0]
        hits = self.get("/search", q="requirements", bid_id=self.bids[0], doc_type=doc_type, top_k=5)["hits"]
        assert hits and all(h["chunk"]["doc_type"] == doc_type for h in hits)
        bad = httpx.get(f"{self.api}/search", params={"q": "x", "config": "nope"}, timeout=60)
        assert bad.status_code == 400 and "error" in bad.json()
        return f"doc_type={doc_type}: {len(hits)} hits; bad config -> 400"

    def check_chunk_and_file(self) -> str:
        hit = self.get("/search", q="due date", bid_id=self.bids[0], top_k=1)["hits"][0]["chunk"]
        chunk = self.get(f"/chunks/{hit['chunk_id']}")
        assert chunk["chunk_id"] == hit["chunk_id"]
        file_response = httpx.get(f"{self.api}/files/{self.bids[0]}/{hit['file_name']}", timeout=60)
        assert file_response.status_code == 200 and len(file_response.content) > 100
        traversal = httpx.get(f"{self.api}/files/{self.bids[0]}/..%2F..%2F.env", timeout=60)
        assert traversal.status_code in (400, 404)
        return f"chunk {hit['chunk_id']} and file served; traversal rejected ({traversal.status_code})"

    def check_records(self) -> str:
        parts = []
        for bid in self.bids:
            record = self.get(f"/bids/{bid}/record")
            fields = record["fields"]
            assert len(fields) == 20, f"{bid}: {len(fields)} fields"
            uncited = [n for n, f in fields.items() if f["value"] is not None and not f["sources"]]
            assert not uncited, f"{bid}: values without citation: {uncited}"
            for name, f in fields.items():
                if f["value"] is None:
                    assert f["status"] in ("not_found", "failed") and f["notes"], f"{bid}/{name}"
                assert 0 <= f["confidence"] <= 1
            parts.append(f"{bid}: {record['validation']}")
        return "; ".join(parts)

    def check_ask(self) -> str:
        bid = self.bids[0]
        answer = self.post("/ask", {"question": "What is the submission deadline after all addendums?", "bid_ids": [bid]})
        assert answer["answer"], "empty answer"
        if answer["answer"].startswith(NOT_FOUND):
            assert not answer["citations"]
        else:
            assert answer["citations"], "found answer without citations"
        return f"{answer['answer'][:90]!r} ({len(answer['citations'])} citations, cached={answer['cached']})"

    def check_ask_cache(self) -> str:
        bid = self.bids[0]
        question = "What is the submission deadline after all addendums?"
        first = self.post("/ask", {"question": question, "bid_ids": [bid]})
        if first["answer"].startswith(NOT_FOUND):
            return "skipped: first answer was not found (never cached)"
        t0 = time.perf_counter()
        second = self.post("/ask", {"question": question + " ", "bid_ids": [bid]})
        assert second["cached"], "second identical question was not served from the cache"
        return f"cache hit in {time.perf_counter() - t0:.1f}s"

    def check_ask_not_found(self) -> str:
        answer = self.post("/ask", {"question": "What is the name of the mayor's pet dog?", "bid_ids": [self.bids[0]]})
        assert answer["answer"].startswith(NOT_FOUND) and not answer["citations"], answer["answer"][:120]
        return "unanswerable question -> Not found, no citations"

    def check_ask_background(self) -> str:
        started = self.post("/ask/runs", {"question": "Is a pre-bid meeting required?", "bid_ids": self.bids[:1]}, expect=202)
        body = self.wait_run(started["run_id"])
        assert body["kind"] == "qa" and body["status"] == "done" and body["answer"], body.get("error")
        if body["answer"]["cached"]:
            # A repeated smoke run meets its own earlier answer in the semantic cache: no graph, no steps.
            assert not body["steps"], "a cached answer must not run the graph"
            return "served from the answer cache (no graph steps); run `python main.py cache clear` for a cold run"
        assert body["summary"] and body["steps"], "no summary / steps"
        nodes = [s["node"] for s in body["steps"]]
        assert "retrieve" in nodes and "answer" in nodes, nodes
        return f"{len(nodes)} steps, {body['summary']['llm_calls']} LLM calls, ${body['summary']['cost_usd']:.4f}"

    def check_question_limit(self) -> str:
        response = httpx.post(f"{self.api}/ask", json={"question": "x" * 5000}, timeout=60)
        assert response.status_code == 422 and "error" in response.json()
        return "oversized question -> 422"

    def check_compare_api(self) -> str:
        report = self.post("/compare", {"bid_ids": self.bids})
        assert report["bid_ids"] == self.bids and len(report["rows"]) == 20
        assert self.get("/compare")["run_id"] == report["run_id"]
        assert self.post("/compare", {"bid_ids": self.bids[:1]}, expect=400)
        return f"{sum(r['differs'] for r in report['rows'])} differing rows, {len(report['highlights'])} highlights"

    def check_go_no_go_api(self) -> str:
        parts = []
        for bid in self.bids:
            report = self.post(f"/bids/{bid}/go-no-go")
            assert report["recommendation"] in ("go", "no_go", "review") and report["criteria"]
            assert self.get(f"/bids/{bid}/go-no-go")["run_id"] == report["run_id"]
            parts.append(f"{bid}: {report['recommendation']} ({report['met']} met/{report['not_met']} not/{report['unknown']} unknown)")
        return "; ".join(parts)

    def check_runs_and_metrics(self) -> str:
        runs = self.get("/runs")
        assert runs, "no runs listed"
        metrics = self.get("/metrics")
        assert "runs" in metrics and "cache" in metrics and "ask_queue" in metrics
        return f"{len(runs)} runs; cache hits={metrics['cache']['hits']} misses={metrics['cache']['misses']}"

    def check_eval_endpoints(self) -> str:
        retrieval = self.get("/eval/retrieval")
        assert retrieval["summary"] and len(retrieval["questions"]) >= 15
        extraction = self.get("/eval/extraction")
        assert extraction["bids"]
        best = max(v.get("recall@5", 0) for v in retrieval["summary"].values())
        return f"{len(retrieval['questions'])} questions, best recall@5={best:.3f}; extraction bids={len(extraction['bids'])}"

    def check_outputs_on_disk(self) -> str:
        expected = [f"{b}.json" for b in self.bids] + [f"{b}_summary.md" for b in self.bids]
        expected += ["comparison.md", "comparison.json"] + [f"{b}_go_no_go.md" for b in self.bids]
        missing = [name for name in expected if not (ROOT / "outputs" / name).is_file()]
        assert not missing, f"missing: {missing}"
        for bid in self.bids:
            json.loads((ROOT / "outputs" / f"{bid}.json").read_text(encoding="utf-8"))
        return f"{len(expected)} files present"

    def check_upload_and_remove(self) -> str:
        """Upload a copy of the first bid as a throwaway bid, then DELETE it."""
        tmp_bid = "SmokeTmp"
        source = ROOT / "data" / self.bids[0]
        files = [("files", (path.name, path.read_bytes())) for path in sorted(source.iterdir())
                 if path.suffix.lower() in (".pdf", ".html", ".htm")]
        started = time.perf_counter()
        upload = httpx.post(f"{self.api}/bids", data={"bid_id": tmp_bid}, files=files, timeout=600)
        elapsed = time.perf_counter() - started
        assert upload.status_code == 202, f"upload -> {upload.status_code}: {upload.text[:200]}"
        run_id = upload.json()["run_id"]
        assert tmp_bid in {r["bid_id"] for r in self.get("/bids")}, "uploaded bid not listed"
        # Indexing runs in the background: the API must stay responsive meanwhile.
        assert self.get("/health")["status"] == "ok", "API unresponsive while indexing"
        assert httpx.delete(f"{self.api}/bids/{tmp_bid}", timeout=30).status_code == 409, \
            "a bid being indexed must not be removable"
        run = self.wait_run(run_id)
        assert run["status"] == "done", f"index run failed: {run.get('error')}"
        chunks = self.get(f"/bids/{tmp_bid}")["chunks"]
        removed = httpx.delete(f"{self.api}/bids/{tmp_bid}", timeout=120)
        assert removed.status_code == 200, f"delete -> {removed.status_code}: {removed.text[:200]}"
        body = removed.json()
        assert body["indexed"] and body["folder_removed"], body
        assert tmp_bid not in {r["bid_id"] for r in self.get("/bids")}, "bid still listed after DELETE"
        assert tmp_bid not in self.get("/health")["indexed_bids"], "bid still indexed after DELETE"
        assert httpx.delete(f"{self.api}/bids/{tmp_bid}", timeout=60).status_code == 404
        assert not (ROOT / "data" / tmp_bid).exists()
        return (f"uploaded {len(files)} files in {elapsed:.1f}s (202, indexed in the background to "
                f"{chunks} chunks), removed; second DELETE -> 404")

    def check_cli_help(self) -> str:
        result = self.cli("--help")
        assert result.returncode == 0
        for command in ("parse", "index", "search", "extract", "ask", "run", "compare", "go-no-go", "eval", "cache"):
            assert command in result.stdout, command
        return "all commands listed"

    def check_cli_parse(self) -> str:
        result = self.cli("parse", "--bid", f"data/{self.bids[0]}")
        assert result.returncode == 0, result.stdout[-300:] + result.stderr[-300:]
        parsed = ROOT / "outputs" / "parsed" / self.bids[0]
        files = list(parsed.glob("*.json"))
        assert files, "no parsed json written"
        return f"{len(files)} parsed files"

    def check_cli_compare(self) -> str:
        result = self.cli("compare", *self.bids)
        assert result.returncode == 0, result.stdout[-300:] + result.stderr[-300:]
        assert "differs" in result.stdout
        return "table printed; outputs/comparison.md rewritten"

    def check_cli_go_no_go(self) -> str:
        result = self.cli("go-no-go", "--bid", self.bids[0])
        assert result.returncode == 0, result.stdout[-300:] + result.stderr[-300:]
        missing = self.cli("go-no-go", "--bid", "NoSuchBid")
        assert missing.returncode == 2
        return "report printed; unknown bid -> exit 2"

    # ------------------------------------------------------------ driver

    def run_all(self) -> None:
        """Every check, LLM ones last so a missing key fails only those."""
        self.run("API: health lists the bids as indexed", self.check_health)
        self.run("API: GET /bids", self.check_bids)
        self.run("API: GET /bids/{id} documents and chunks", self.check_bid_detail)
        self.run("API: POST /index is incremental (0 chunks added)", self.check_index_incremental)
        self.run("API: search in all three retrieval configs", self.check_search_configs)
        self.run("API: search metadata filter and bad config", self.check_search_filters)
        self.run("API: chunk lookup, file serving, path traversal rejected", self.check_chunk_and_file)
        self.run("API: saved records have 20 fields, every value cited", self.check_records)
        self.run("API: eval results endpoints", self.check_eval_endpoints)
        self.run("API: oversized question rejected", self.check_question_limit)
        self.run("API: upload a throwaway bid, then DELETE /bids/{id}", self.check_upload_and_remove)
        self.run("CLI: --help lists every command", self.check_cli_help)
        self.run("CLI: parse writes outputs/parsed", self.check_cli_parse)
        if not self.skip_llm:
            self.run("API: POST /ask with citations", self.check_ask)
            self.run("API: semantic answer cache hit", self.check_ask_cache)
            self.run("API: unanswerable question -> Not found", self.check_ask_not_found)
            self.run("API: background question with trace and summary", self.check_ask_background)
            self.run("API: comparison agent", self.check_compare_api)
            self.run("API: go/no-go agent", self.check_go_no_go_api)
            self.run("CLI: compare", self.check_cli_compare)
            self.run("CLI: go-no-go", self.check_cli_go_no_go)
        self.run("API: runs list and metrics", self.check_runs_and_metrics)
        self.run("Outputs on disk", self.check_outputs_on_disk)

    def write_report(self) -> None:
        """eval/results/smoke_e2e.md."""
        passed = sum(c.ok for c in self.checks)
        lines = ["# End-to-end smoke test", "",
                 f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}; API {self.api}; "
                 f"bids {', '.join(self.bids)}; {passed}/{len(self.checks)} checks passed"
                 + ("; LLM checks skipped" if self.skip_llm else "") + ".", "",
                 "| check | result | detail | seconds |", "|---|---|---|---|"]
        for c in self.checks:
            lines.append(f"| {c.name} | {'PASS' if c.ok else 'FAIL'} | {c.detail.replace('|', '/')} | {c.seconds} |")
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8001")
    parser.add_argument("--bids", nargs="+", default=["Bid1", "Bid2"])
    parser.add_argument("--skip-llm", action="store_true", help="skip the checks that call the LLM")
    args = parser.parse_args()
    smoke = Smoke(api=args.api.rstrip("/"), bids=list(args.bids), skip_llm=args.skip_llm)
    smoke.run_all()
    smoke.write_report()
    failed = [c for c in smoke.checks if not c.ok]
    print(f"\n{len(smoke.checks) - len(failed)}/{len(smoke.checks)} checks passed -> {RESULTS}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
