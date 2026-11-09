"""Tests for the BM25 tokenizer and store."""

from rfp_intel.schemas import Chunk, DocType, SearchFilters
from rfp_intel.search.bm25_store import BM25Store, tokenize


def test_ids_stay_whole_and_parts_are_added():
    assert tokenize("RFP JA-207652") == ["rfp", "ja-207652", "ja", "207652"]


def test_hash_suffix_and_alphanumeric_codes():
    assert tokenize("SI# CC7802 WD22TB4") == ["si#", "si", "cc7802", "wd22tb4"]


def test_punctuation_and_case():
    assert tokenize("Net 30. Invoices: STOaccountspayable@treasurer.md") == [
        "net", "30", "invoices", "stoaccountspayable", "treasurer", "md"
    ]


def test_sku_with_letters():
    assert "210-blyz" in tokenize("Base 210-BLYZ") and "blyz" in tokenize("Base 210-BLYZ")


def _chunk(cid: str, bid: str, text: str, doc_type=DocType.RFP) -> Chunk:
    return Chunk(chunk_id=cid, bid_id=bid, file_name="f.pdf", doc_type=doc_type, addendum_number=None,
                 page=1, doc_date=None, section=None, kind="text", header="", text=text, token_count=1)


def test_store_search_filters_and_multi_bid(tmp_path):
    store = BM25Store(tmp_path)
    a = [_chunk("a1", "A", "docking station WD22TB4 quantity 30"), _chunk("a2", "A", "warranty terms"),
         _chunk("a3", "A", "processor specs", DocType.SPECS)]
    b = [_chunk("b1", "B", "WD22TB4 dock for staff")]
    store.save("A", a, [c.text for c in a])
    store.save("B", b, [c.text for c in b])

    assert store.search("WD22TB4", SearchFilters(bid_id="A"), 5)[0][0].chunk_id == "a1"
    both = {c.chunk_id for c, _ in store.search("WD22TB4", None, 5)}
    assert both == {"a1", "b1"}
    specs = store.search("processor", SearchFilters(bid_id="A", doc_types=[DocType.SPECS]), 5)
    assert [c.chunk_id for c, _ in specs] == ["a3"]
    assert store.search("nothing matches", SearchFilters(bid_id="A"), 5) == []
    assert store.search("x", SearchFilters(bid_id="missing"), 5) == []


def test_store_delete_and_reload(tmp_path):
    store = BM25Store(tmp_path)
    store.save("A", [_chunk("a1", "A", "alpha")], ["alpha"])
    assert BM25Store(tmp_path).bids() == ["A"]
    store.delete("A")
    assert store.bids() == [] and store.search("alpha", None, 5) == []
