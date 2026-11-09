"""One bad file must never fail a run: empty, corrupt, encrypted, scanned, unknown, duplicate."""

import logging
import shutil

import pymupdf
import pytest

from rfp_intel.config import settings
from rfp_intel.ingestion.loader import load_bid_folder
from rfp_intel.schemas import DocType


@pytest.fixture
def messy_bid(tmp_path, mini_bid):
    folder = tmp_path / "MessyBid"
    folder.mkdir()
    shutil.copy(mini_bid / "rfp.pdf", folder / "rfp.pdf")
    shutil.copy(mini_bid / "rfp.pdf", folder / "rfp_copy.pdf")          # duplicate bytes
    shutil.copy(mini_bid / "bid_page.html", folder / "bid_page.html")
    (folder / "empty.pdf").write_bytes(b"")
    (folder / "corrupt.pdf").write_bytes(b"this is not a pdf at all")
    (folder / "notes.docx").write_bytes(b"PK\x03\x04 fake word file")

    scanned = pymupdf.open()
    scanned.new_page()
    scanned.new_page()
    scanned.save(folder / "scanned.pdf")

    locked = pymupdf.open()
    locked.new_page().insert_text((50, 60), "secret content")
    locked.save(folder / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
                user_pw="pw", owner_pw="owner")
    return folder


def _by_name(folder):
    return {d.file_name: d for d in load_bid_folder(folder)}


def test_bad_files_do_not_stop_the_good_ones(messy_bid):
    docs = _by_name(messy_bid)
    assert docs["rfp.pdf"].doc_type is DocType.RFP and len(docs["rfp.pdf"].pages) == 2
    assert docs["bid_page.html"].doc_type is DocType.BID_PAGE
    assert all(d.bid_id == "MessyBid" for d in docs.values())


@pytest.mark.parametrize("name", ["empty.pdf", "corrupt.pdf"])
def test_unreadable_file_becomes_document_with_warning(messy_bid, name):
    doc = _by_name(messy_bid)[name]
    assert doc.pages == []
    assert doc.warnings and doc.warnings[0].startswith("could not parse file")


def test_encrypted_pdf_is_reported(messy_bid):
    doc = _by_name(messy_bid)["locked.pdf"]
    assert doc.pages == []
    assert "password" in doc.warnings[0]


def test_scanned_pdf_is_flagged(messy_bid):
    doc = _by_name(messy_bid)["scanned.pdf"]
    assert all(p.is_empty for p in doc.pages)
    assert "no extractable text (scanned or empty file)" in doc.warnings


def test_unknown_extension_and_duplicate_are_skipped_with_log(messy_bid, caplog):
    with caplog.at_level(logging.WARNING):
        docs = _by_name(messy_bid)
    assert "notes.docx" not in docs
    assert len([n for n in docs if n.startswith("rfp")]) == 1
    assert "unsupported file type" in caplog.text
    assert "duplicate of" in caplog.text


def test_ocr_failure_warns_instead_of_failing(messy_bid, monkeypatch):
    import pytesseract

    def broken(*_args, **_kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(settings.ingestion.ocr, "enabled", True)
    monkeypatch.setattr(pytesseract, "image_to_string", broken)
    doc = _by_name(messy_bid)["scanned.pdf"]
    assert all(p.is_empty for p in doc.pages)
    assert any(w.startswith("OCR failed on page 1") for w in doc.warnings)


def test_ocr_text_fills_empty_pages_when_enabled(messy_bid, monkeypatch):
    import pytesseract

    calls = iter(["Scanned page one text", "Scanned page two text"])
    monkeypatch.setattr(settings.ingestion.ocr, "enabled", True)
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *_a, **_k: next(calls))
    doc = _by_name(messy_bid)["scanned.pdf"]
    assert [p.text for p in doc.pages] == ["Scanned page one text", "Scanned page two text"]
    assert not any(p.is_empty for p in doc.pages)


def test_missing_folder_raises_clear_error(tmp_path):
    with pytest.raises(NotADirectoryError):
        load_bid_folder(tmp_path / "nope")
