"""Fakes for graph tests: an in-memory search engine and a scripted LLM."""

from __future__ import annotations

import re

from rfp_intel import llm
from rfp_intel.schemas import (
    AnswerDraft,
    Chunk,
    ComparisonDraft,
    ComparisonHighlightDraft,
    CriterionCheck,
    CriterionChecks,
    DocType,
    FieldExtraction,
    FieldExtractions,
    IndexStats,
    QAPlan,
    ReconcileDecision,
    ReconcileDecisions,
    SearchHit,
    SupportCheck,
    SupportChecks,
    Usage,
)

RFP_TEXT = ("Proposals are due June 27, 2024 at 2:00 PM CST. The term is three years. "
            "Contact: Pat Buyer, buyer@example.org, (555) 123-4567. Issued by Example County.")
ADDENDUM_TEXT = "ADDENDUM No. 2 The new due date for this RFP will be July 9, 2024 at 2:00 PM CST."
SPECS_TEXT = "Product: 100 Model X laptops, SKU ABC-123. Memory 16GB RAM."


def _chunk(cid: str, bid: str, file: str, text: str, doc_type: DocType, page: int = 1, addendum: int | None = None) -> Chunk:
    return Chunk(chunk_id=cid, bid_id=bid, file_name=file, doc_type=doc_type, addendum_number=addendum, page=page,
                 doc_date=None, section=None, kind="text", header=f"{bid} | {doc_type.value} | {file} | p{page} | ",
                 text=text, token_count=len(text.split()))


class FakeAnswerCache:
    """In-memory stand-in for the semantic answer cache (exact-question matches)."""

    def __init__(self):
        self.items: dict[tuple[str, str, str], object] = {}
        self.gets = 0

    def get(self, question, bids, fingerprint):
        self.gets += 1
        hit = self.items.get((question.strip().lower(), ",".join(sorted(bids)), fingerprint))
        return hit.model_copy(update={"cached": True, "question": question}) if hit is not None else None

    def put(self, question, bids, fingerprint, answer):
        if not answer.answer.startswith("Not found"):
            self.items[(question.strip().lower(), ",".join(sorted(bids)), fingerprint)] = answer

    def count(self):
        return len(self.items)

    def clear(self):
        n = len(self.items)
        self.items.clear()
        return n


class FakeEngine:
    """Returns the same small corpus for every query; addendum filter returns only the addendum."""

    def __init__(self, bid: str = "BidT", empty: bool = False):
        self.bid, self.empty = bid, empty
        self.searches: list[tuple[str, dict]] = []
        self.answer_cache = FakeAnswerCache()
        self.fingerprint = "fp-1"
        self.chunks = [
            _chunk("r:2:0", bid, "rfp.pdf", RFP_TEXT, DocType.RFP, page=2),
            _chunk("a:1:0", bid, "addendum_2.pdf", ADDENDUM_TEXT, DocType.ADDENDUM, addendum=2),
            _chunk("s:1:0", bid, "specs.pdf", SPECS_TEXT, DocType.SPECS),
        ]

    def index_bid(self, folder, force=False, should_stop=None):
        return IndexStats(bid_id=folder.name, documents=3, chunks_added=3, chunks_total=3)

    def warm_up(self):
        pass

    def index_fingerprint(self, bids):
        return self.fingerprint

    def known_bids(self):
        return [self.bid]

    def remove_bid(self, bid_id):
        """True once for the known bid, then False (it is no longer indexed)."""
        if bid_id != self.bid or getattr(self, "removed", False):
            return False
        self.removed = True
        return True

    def search(self, query, filters=None, top_k=None, config=None):
        self.searches.append((query, filters.model_dump(exclude_none=True) if filters else {}))
        if self.empty:
            return []
        wanted = filters.doc_types if filters and filters.doc_types else None
        hits = [c for c in self.chunks if wanted is None or c.doc_type in wanted]
        if filters and filters.bid_id and filters.bid_id != self.bid:
            return []
        return [SearchHit(chunk=c, score=float(len(hits) - i), ranks={"rerank": i + 1}) for i, c in enumerate(hits)]

    def search_many(self, queries, filters=None, top_k=None, config=None):
        merged: dict[str, SearchHit] = {}
        for query in queries:
            for hit in self.search(query, filters, top_k, config):
                merged.setdefault(hit.chunk.chunk_id, hit)
        return list(merged.values())[: top_k or 5]

    def close(self):
        pass


KNOWN_VALUES = {
    "Due Date": ("June 27, 2024 at 2:00 PM CST", "due June 27, 2024 at 2:00 PM CST"),
    "Term of Bid": ("three years", "The term is three years"),
    "contact_info": ("Pat Buyer, buyer@example.org", "Pat Buyer, buyer@example.org"),
    "company_name": ("Example County", "Issued by Example County"),
    "Product": ("100 Model X laptops", "100 Model X laptops"),
    "Model_no": ("Model X", "Model X"),
    "Part_no": ("ABC-123", "SKU ABC-123"),
}


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def evidence_id_for(prompt: str, quote: str) -> str | None:
    """The [E<n>] block of the prompt whose text contains the quote."""
    for match in re.finditer(r"\[(E\d+)\][^\n]*\n(.*?)(?=\n\[E\d+\]|\n\nOutput schema|\Z)", prompt, re.S):
        if _squash(quote) in _squash(match.group(2)):
            return match.group(1)
    return None


def requested_fields(prompt: str) -> list[str]:
    """Field names listed as '- <name>:' lines in an extraction prompt."""
    section = prompt.split("Fields to extract", 1)[-1].split("Evidence passages", 1)[0]
    return [m.group(1) for m in re.finditer(r"^- ([^:\n]+):", section, re.M)]


class ScriptedLLM:
    """Stands in for llm.structured / llm.text; `overrides` tweak values per field per call."""

    def __init__(self, overrides: dict[str, list[tuple[str, str]]] | None = None, fail: Exception | None = None,
                 reject_answer_once: bool = False, criterion_verdicts: dict[str, str] | None = None,
                 unsupported_items: dict[str, list[str]] | None = None):
        self.overrides = overrides or {}
        self.unsupported_items = unsupported_items or {}   # field -> items the support check rejects (once)
        self.criterion_verdicts = criterion_verdicts
        self.fail = fail
        self.reject_answer_once = reject_answer_once
        self.calls: list[str] = []
        self.prompts: list[tuple[str, str]] = []

    def structured(self, prompt, model_cls, model, system=None):
        self.calls.append(model_cls.__name__)
        self.prompts.append((model_cls.__name__, prompt))
        if self.fail is not None:
            raise self.fail
        usage = Usage(tokens_in=10, tokens_out=5)
        if model_cls is FieldExtractions:
            return self._extractions(prompt), usage
        if model_cls is ReconcileDecisions:
            eid = evidence_id_for(prompt, "July 9, 2024") or "E1"
            items = [ReconcileDecision(field="Due Date", kind="override", new_value="July 9, 2024 at 2:00 PM CST",
                                       old_value=None, evidence_id=eid, quote="new due date for this RFP will be July 9, 2024 at 2:00 PM CST",
                                       reason="extended")] if "- Due Date:" in prompt else []
            return ReconcileDecisions(items=items), usage
        if model_cls is SupportChecks:
            names = re.findall(r"^- ([^:\n]+): value=", prompt, re.M)
            if self.reject_answer_once and names == ["answer"]:
                self.reject_answer_once = False
                return SupportChecks(items=[SupportCheck(field="answer", supported=False,
                                                         reason="the evidence does not mention a portal")]), usage
            items = []
            for n in names:
                rejected = self.unsupported_items.pop(n, None)
                items.append(SupportCheck(field=n, supported=False, reason="item not in evidence",
                                          unsupported_items=rejected) if rejected
                             else SupportCheck(field=n, supported=True))
            return SupportChecks(items=items), usage
        if model_cls is QAPlan:
            return QAPlan(question_type="single", bids=[], doc_types=[], queries=["due date"]), usage
        if model_cls is AnswerDraft:
            eid = evidence_id_for(prompt, "July 9, 2024") or "E1"
            return AnswerDraft(answer="The due date is July 9, 2024 at 2:00 PM CST.", found=True,
                               evidence_ids=[eid], quotes=["July 9, 2024 at 2:00 PM CST"]), usage
        if model_cls is ComparisonDraft:
            return ComparisonDraft(
                narrative="Both bids procure laptops; the due dates differ.",
                highlights=[ComparisonHighlightDraft(field="Due Date", note="BidT is due July 9, 2024; the other is not stated."),
                            ComparisonHighlightDraft(field="No Such Field", note="dropped by code")]), usage
        if model_cls is CriterionChecks:
            ids = re.findall(r"^- id: (\S+)", prompt, re.M)
            verdict = self.criterion_verdicts or {}
            return CriterionChecks(items=[CriterionCheck(criterion=i, status=verdict.get(i, "met"),
                                                         reason=f"{i} judged from the values") for i in ids]), usage
        raise AssertionError(f"unexpected model {model_cls}")

    def text(self, prompt, model, system=None):
        self.calls.append("text")
        if self.fail is not None:
            raise self.fail
        return "This bid procures laptops. Proposals are due July 9, 2024.", Usage(tokens_in=5, tokens_out=5)

    def _extractions(self, prompt: str) -> FieldExtractions:
        items = []
        for name in requested_fields(prompt):
            value, quote = KNOWN_VALUES.get(name, (None, None))
            if name in self.overrides and self.overrides[name]:
                value, quote = self.overrides[name].pop(0)
            eid = evidence_id_for(prompt, quote) if quote else None
            if value is None:
                items.append(FieldExtraction(field=name, value=None, evidence_ids=[], quotes=[], status="not_found"))
            else:
                items.append(FieldExtraction(field=name, value=value, evidence_ids=[eid] if eid else ["E99"],
                                             quotes=[quote], status="ok"))
        return FieldExtractions(items=items)


def install(monkeypatch, scripted: ScriptedLLM) -> None:
    """Route every LLM call through the scripted fake."""
    monkeypatch.setattr(llm, "structured", scripted.structured)
    monkeypatch.setattr(llm, "text", scripted.text)
