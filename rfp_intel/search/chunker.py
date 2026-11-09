"""Structure-aware chunking (BUILD_PLAN 6.2).

A document is chunked as one stream of lines (not page by page) so units that
cross a page break stay whole; a chunk's `page` is the page it starts on.

Special units are kept whole and split only above `max_tokens`:
- qa:        addendum question + "Answer:" (numbered questions, in sequence)
- spec_list: a "...Requirements" heading and its bullets, across page breaks
- table:     real tables (not layout grids) as "a | b | c" rows, header repeated
- record:    the HTML bid page
Remaining text is packed paragraph by paragraph (then sentence by sentence)
up to `max_tokens`, with `overlap_tokens` carried into the next chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import tiktoken

from rfp_intel.config import settings
from rfp_intel.schemas import Chunk, DocType, Document

_QA_START = re.compile(r"^(\d{1,3})\.\s+\S")
_ANSWER = re.compile(r"^answer\s*:", re.IGNORECASE)
_BULLET = re.compile(r"^(?:[•▪●◦–—\-*]\s*|[a-z0-9]{1,2}[.)]\s+)\S", re.IGNORECASE)
_NUMBERED_HEADING = re.compile(r"^\d{1,2}(?:\.\d{1,2})*\s+[A-Z]")
_SPEC_HEADING = re.compile(r"requirements:?$", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
_MAX_HEADING_LINES = 3
_ALWAYS_KEEP = {"qa", "record"}    # exempt from the min_tokens rule


@dataclass
class _Line:
    """One text line with its page and whether it starts that page."""

    text: str
    page: int
    page_start: bool = False
    page_break: bool = False           # blank line inserted between pages (not a paragraph end)


@dataclass
class _Block:
    """A unit found in the line stream before packing."""

    kind: str                          # text | qa | spec_list
    section: str | None
    lines: list[_Line] = field(default_factory=list)


@dataclass
class _Draft:
    """A chunk before it gets ids and a header."""

    kind: str
    page: int
    section: str | None
    text: str


# ------------------------------------------------------------------ public API


def chunk_document(doc: Document) -> list[Chunk]:
    """All chunks for one parsed document."""
    if doc.doc_type == DocType.BID_PAGE:
        drafts = _record_drafts(doc)
    else:
        lines = _lines(doc)
        blocks, page_sections = _blocks(lines, is_addendum=doc.doc_type == DocType.ADDENDUM)
        drafts = _pack_blocks(blocks) + _table_drafts(doc, page_sections)
    return [_to_chunk(doc, seq, draft) for seq, draft in enumerate(_keep(drafts))]


def _keep(drafts: list[_Draft]) -> list[_Draft]:
    """Drop chunks below min_tokens (except qa/record) and exact duplicates."""
    seen: set[tuple[str, str]] = set()
    kept: list[_Draft] = []
    for draft in drafts:
        key = (draft.kind, draft.text)
        if key in seen:
            continue
        if draft.kind not in _ALWAYS_KEEP and count_tokens(draft.text) < settings.chunking.min_tokens:
            continue
        seen.add(key)
        kept.append(draft)
    return kept


def detect_sections(text: str) -> list[tuple[str | None, str]]:
    """Split text into (heading, body) sections; consecutive heading lines merge."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if is_heading(lines[i]):
            heading, i = _merged_heading(lines, i)
            sections.append((heading, []))
            continue
        sections[-1][1].append(lines[i])
        i += 1
    return [(h, "\n".join(body).strip()) for h, body in sections if h or "\n".join(body).strip()]


def is_heading(line: str) -> bool:
    """Heading: mostly-uppercase line (3-80 chars), numbered heading, or "...Requirements"."""
    text = line.strip()
    if not text or _BULLET.match(text) or _QA_START.match(text):
        return False
    if _SPEC_HEADING.search(text) and len(text) <= 120:
        return True
    if _NUMBERED_HEADING.match(text) and len(text) <= 80:
        return True
    letters = [c for c in text if c.isalpha()]
    visible = [c for c in text if not c.isspace()]
    return 3 <= len(text) <= 80 and len(letters) >= 3 and (
        sum(c.isupper() for c in letters) >= 0.7 * len(letters)
        and len(letters) >= 0.5 * len(visible)     # not a date/number line like "06/01/2024 at 2:00 PM EDT"
    )


def count_tokens(text: str) -> int:
    """Token count with the cl100k_base encoding."""
    return len(_encoding().encode(text))


def chunk_header(chunk: Chunk) -> str:
    """Context header: "Bid1 | addendum 1 | file | p2 | Q31"."""
    number = f" {chunk.addendum_number}" if chunk.addendum_number else ""
    return (f"{chunk.bid_id} | {chunk.doc_type.value}{number} | {chunk.file_name} | "
            f"p{chunk.page} | {chunk.section or ''}")


def embed_text(chunk: Chunk) -> str:
    """Text that is embedded and BM25-indexed: header + newline + text."""
    return f"{chunk.header}\n{chunk.text}"


# ------------------------------------------------------------------ line stream


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    """The cl100k_base tokenizer (cached)."""
    return tiktoken.get_encoding("cl100k_base")


def _lines(doc: Document) -> list[_Line]:
    """Lines of all non-empty pages; a blank line marks a paragraph break."""
    lines: list[_Line] = []
    for page in doc.pages:
        if page.is_empty or not page.text.strip():
            continue
        if lines and lines[-1].text:
            lines.append(_Line("", page.number, page_break=True))
        for n, text in enumerate(page.text.splitlines()):
            lines.append(_Line(text.strip(), page.number, page_start=n == 0))
    return lines


def _merged_heading(lines: list[str], i: int) -> tuple[str, int]:
    """Join consecutive uppercase heading lines ("SCOPE AND ... OF THE" + "PROPOSAL")."""
    parts = [lines[i].strip()]
    j = i + 1
    while (j < len(lines) and len(parts) < _MAX_HEADING_LINES and _is_upper_heading(parts[-1])
           and _is_upper_heading(lines[j]) and not _SPEC_HEADING.search(lines[j])):
        parts.append(lines[j].strip())
        j += 1
    return " ".join(parts), j


def _is_upper_heading(line: str) -> bool:
    """Heading made of uppercase words (not numbered, not a spec heading)."""
    text = line.strip()
    return is_heading(text) and not _NUMBERED_HEADING.match(text) and not _SPEC_HEADING.search(text)


def _blocks(lines: list[_Line], is_addendum: bool) -> tuple[list[_Block], dict[int, str | None]]:
    """Split the line stream into text, qa and spec_list blocks.

    Also returns the section active at the start of each page (for tables).
    """
    blocks: list[_Block] = []
    page_sections: dict[int, str | None] = {}
    section: str | None = None
    para: list[_Line] = []

    def flush() -> None:
        nonlocal para
        if any(line.text for line in para):
            blocks.append(_Block("text", section, para))
        para = []

    i = 0
    while i < len(lines):
        line = lines[i]
        page_sections.setdefault(line.page, section)
        qa = _QA_START.match(line.text) if is_addendum else None
        if qa:
            flush()
            end = _collect_qa(lines, i, int(qa.group(1)))
            blocks.append(_Block("qa", f"Q{qa.group(1)}", lines[i:end]))
            i = end
            continue
        if is_heading(line.text):
            heading, j = _merged_heading([ln.text for ln in lines], i)
            if _SPEC_HEADING.search(heading):
                flush()
                spec_lines, end = _collect_spec(lines, i, j)
                blocks.append(_Block("spec_list", heading, spec_lines))
                section = heading
                i = end
                continue
            flush()
            section = heading
            para = list(lines[i:j])
            i = j
            continue
        para.append(line)
        i += 1
    flush()
    return blocks, page_sections


def _collect_qa(lines: list[_Line], start: int, number: int) -> int:
    """End index of a Q&A unit: the next question (number + 1) or a paragraph
    break after the answer. A list restarting at "1." inside the answer belongs
    to the answer until the next paragraph break. Page breaks do not end a unit."""
    seen_answer = inner_list = False
    i = start + 1
    while i < len(lines):
        line = lines[i]
        next_q = _QA_START.match(line.text)
        if next_q and seen_answer and int(next_q.group(1)) == 1:
            inner_list = True
        elif next_q and int(next_q.group(1)) == number + 1 and not inner_list:
            break
        if _ANSWER.match(line.text):
            seen_answer = True
        elif not line.text and not line.page_break and seen_answer:
            break
        i += 1
    return i


def _collect_spec(lines: list[_Line], start: int, body: int) -> tuple[list[_Line], int]:
    """Heading lines + bullets (and wrapped bullet lines), skipping running headers at page tops."""
    kept = list(lines[start:body])
    i, end = body, body
    while i < len(lines):
        text = lines[i].text
        if _BULLET.match(text):
            kept.append(lines[i])
            i = end = i + 1
            continue
        if not text:
            i += 1
            continue
        if is_heading(text):
            resume = _skip_page_top_headings(lines, i)
            if resume is None:
                break
            i = resume
            continue
        if lines[i - 1].text and (lines[i - 1] in kept) and not lines[i].page_start:
            kept.append(lines[i])          # wrapped continuation of the previous bullet
            i = end = i + 1
            continue
        break
    return kept, end


def _skip_page_top_headings(lines: list[_Line], i: int) -> int | None:
    """If line i starts a run of headings at the top of a page followed by a bullet,
    return the bullet's index; otherwise None."""
    k = i - 1
    while k >= 0 and not lines[k].page_start and (not lines[k].text or is_heading(lines[k].text)):
        k -= 1
    at_page_top = lines[i].page_start or (k >= 0 and lines[k].page_start and is_heading(lines[k].text))
    if not at_page_top:
        return None
    m = i
    while m < len(lines) and m - i < 4 and (not lines[m].text or is_heading(lines[m].text)):
        m += 1
    return m if m < len(lines) and _BULLET.match(lines[m].text) else None


# ------------------------------------------------------------------ packing


def _pack_blocks(blocks: list[_Block]) -> list[_Draft]:
    """Special blocks become their own chunks; text blocks are packed with overlap."""
    packer = _TextPacker()
    drafts: list[_Draft] = []
    for block in blocks:
        if block.kind == "text":
            for page, paragraph in _paragraphs(block.lines):
                drafts.extend(packer.add(paragraph, page, block.section))
            continue
        drafts.extend(packer.finish())
        text = "\n".join(line.text for line in block.lines if line.text)
        page = block.lines[0].page
        for piece in _split_lines(text, settings.chunking.max_tokens):
            drafts.append(_Draft(block.kind, page, block.section, piece))
    drafts.extend(packer.finish())
    return drafts


def _paragraphs(lines: list[_Line]) -> list[tuple[int, str]]:
    """(page, text) for each blank-line-separated paragraph."""
    out: list[tuple[int, str]] = []
    current: list[_Line] = []
    for line in lines + [_Line("", 0)]:
        if line.text:
            current.append(line)
        elif current:                      # paragraph or page break: page numbers stay exact
            out.append((current[0].page, "\n".join(ln.text for ln in current)))
            current = []
    return out


class _TextPacker:
    """Packs paragraphs into chunks of <= max_tokens, each seeded with the
    previous chunk's last `overlap_tokens` tokens."""

    def __init__(self) -> None:
        cfg = settings.chunking
        self.max, self.overlap, self.min = cfg.max_tokens, cfg.overlap_tokens, cfg.min_tokens
        self.overlap_text = ""
        self.overlap_size = 0
        self.new_parts: list[str] = []     # text added since the last emit (no overlap)
        self.new_tokens = 0
        self.page = 1
        self.section: str | None = None
        self.last: _Draft | None = None

    def add(self, paragraph: str, page: int, section: str | None) -> list[_Draft]:
        """Add a paragraph; return the chunks it completed."""
        out: list[_Draft] = []
        if section != self.section and self.new_tokens >= self.min:
            out.extend(self._emit())
        for piece in _split_text(paragraph, self.max - self.overlap):
            size = count_tokens(piece) + 1     # +1 for the joining newline
            if self.new_parts and self.overlap_size + self.new_tokens + size > self.max:
                out.extend(self._emit())
            if not self.new_parts:
                self.page, self.section = page, section
            self.new_parts.append(piece)
            self.new_tokens += size
        return out

    def finish(self) -> list[_Draft]:
        """Close the stream (before a special unit or at the end).

        A tail below `min_tokens` is appended to the previous chunk when it
        fits, otherwise dropped (BUILD_PLAN: drop chunks < min_tokens).
        """
        out: list[_Draft] = []
        if self.new_tokens >= self.min:
            out = self._emit()
        elif self.new_parts and self.last is not None and (
            count_tokens(self.last.text) + self.new_tokens <= self.max
        ):
            self.last.text += "\n" + "\n".join(self.new_parts)
        self.overlap_text, self.overlap_size = "", 0
        self.new_parts, self.new_tokens = [], 0
        return out

    def _emit(self) -> list[_Draft]:
        """Close the current chunk; its tail becomes the next chunk's overlap."""
        parts = ([self.overlap_text] if self.overlap_text else []) + self.new_parts
        draft = _Draft("text", self.page, self.section, "\n".join(parts).strip())
        self.last = draft
        self.overlap_text = _token_tail(draft.text, self.overlap)
        self.overlap_size = count_tokens(self.overlap_text) if self.overlap_text else 0
        self.new_parts, self.new_tokens = [], 0
        return [draft]


def _token_tail(text: str, n: int) -> str:
    """Last ~n tokens of text, starting at a word boundary."""
    if n <= 0:
        return ""
    ids = _encoding().encode(text)
    if len(ids) <= n:
        return ""
    tail = _encoding().decode(ids[-n:])
    cut = re.search(r"\s", tail)
    return tail[cut.end():].strip() if cut else tail.strip()


def _split_text(text: str, max_tokens: int) -> list[str]:
    """Pieces of <= max_tokens: whole text, else sentences, else hard token splits."""
    if count_tokens(text) <= max_tokens:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if count_tokens(candidate) <= max_tokens:
            current = candidate
            continue
        if current:
            pieces.append(current)
        if count_tokens(sentence) <= max_tokens:
            current = sentence
        else:
            pieces.extend(_hard_split(sentence, max_tokens))
            current = ""
    if current:
        pieces.append(current)
    return pieces


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """Split by token count when there is no sentence boundary."""
    ids = _encoding().encode(text)
    return [_encoding().decode(ids[i:i + max_tokens]).strip() for i in range(0, len(ids), max_tokens)]


def _split_lines(text: str, max_tokens: int, header: str | None = None) -> list[str]:
    """Split a unit by lines into pieces <= max_tokens; `header` repeats on each piece."""
    if count_tokens(text) <= max_tokens:
        return [text]
    pieces: list[str] = []
    current: list[str] = [header] if header else []
    base = count_tokens(header) if header else 0
    size = base
    for line in text.splitlines():
        if header and line == header and not pieces and current == [header]:
            continue
        for part in _split_text(line, max_tokens - base):
            n = count_tokens(part) + 1         # +1 for the joining newline
            if size + n > max_tokens and len(current) > (1 if header else 0):
                pieces.append("\n".join(current))
                current, size = ([header] if header else []), base
            current.append(part)
            size += n
    if len(current) > (1 if header else 0):
        tail = "\n".join(current)
        if pieces and count_tokens(tail) < settings.chunking.min_tokens:
            body = current[1:] if header else current
            pieces[-1] += "\n" + "\n".join(body)   # a tiny tail joins the previous piece
        else:
            pieces.append(tail)
    return pieces


# ------------------------------------------------------------------ tables & records


def _table_drafts(doc: Document, page_sections: dict[int, str | None]) -> list[_Draft]:
    """One or more table chunks per real table, header row repeated when split."""
    drafts: list[_Draft] = []
    for page in doc.pages:
        for table in page.tables:
            rows = [" | ".join(cell for cell in row if cell) for row in table]
            rows = [row for row in rows if row]
            if len(rows) < 2 or not _is_real_table(table):
                continue
            text = "\n".join(rows)
            for piece in _split_lines(text, settings.chunking.max_tokens, header=rows[0]):
                drafts.append(_Draft("table", page.number, page_sections.get(page.number), piece))
    return drafts


def _is_real_table(table: list[list[str]]) -> bool:
    """At least half the rows have two or more non-empty cells (not a layout grid)."""
    rows = [row for row in table if any(row)]
    multi = sum(1 for row in rows if sum(1 for cell in row if cell) >= 2)
    return bool(rows) and multi >= 0.5 * len(rows)


def _record_drafts(doc: Document) -> list[_Draft]:
    """The HTML bid page as one record chunk (split by lines only if too long)."""
    drafts: list[_Draft] = []
    for page in doc.pages:
        if not page.text.strip():
            continue
        for piece in _split_lines(page.text, settings.chunking.max_tokens):
            drafts.append(_Draft("record", page.number, doc.title, piece))
    return drafts


def _to_chunk(doc: Document, seq: int, draft: _Draft) -> Chunk:
    """Finalize a draft into a Chunk with id, header and token count."""
    chunk = Chunk(
        chunk_id=f"{doc.doc_id}:{draft.page}:{seq}",
        bid_id=doc.bid_id,
        file_name=doc.file_name,
        doc_type=doc.doc_type,
        addendum_number=doc.addendum_number,
        page=draft.page,
        doc_date=doc.doc_date,
        section=draft.section,
        kind=draft.kind,  # type: ignore[arg-type]
        header="",
        text=draft.text,
        token_count=count_tokens(draft.text),
    )
    chunk.header = chunk_header(chunk)
    return chunk
