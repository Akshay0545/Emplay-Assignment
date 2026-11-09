"""HTML parsing: targeted label/value extraction from bid portal pages.

Only labelled values are kept, so navigation, account links and cookie
banners never reach the output. The configured drop selectors remove page
chrome, except elements that contain field labels: portals often wrap the
whole page in a <form>, and dropping it would delete every field.
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from rfp_intel.config import load_ingestion, settings
from rfp_intel.schemas import Page


def parse_html(path: Path, warnings: list[str] | None = None) -> tuple[list[Page], dict[str, str]]:
    """Parse an HTML page into one Page of `Label: Value` lines plus a fields dict."""
    warnings = warnings if warnings is not None else []
    html = Path(path).read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    labels = load_ingestion().html_labels
    _drop_chrome(soup, labels)

    fields: dict[str, str] = {}
    for label in labels:
        value = _label_value(soup, label)
        if value:
            fields[label] = value
    for key, value in _pair_fields(soup):
        fields.setdefault(key, value)

    text = "\n".join(f"{key}: {value}" for key, value in fields.items())
    is_empty = len(text) < settings.ingestion.min_page_chars
    if is_empty:
        warnings.append("no label/value fields found in HTML page")
    return [Page(number=1, text=text, is_empty=is_empty)], fields


def _drop_chrome(soup: BeautifulSoup, labels: list[str]) -> None:
    """Remove configured selectors unless the element holds field labels."""
    for selector in load_ingestion().html_drop_selectors:
        for element in soup.select(selector):
            if element.decomposed:
                continue
            if _count_labels(element, labels) == 0:
                element.decompose()


def _count_labels(element: Tag, labels: list[str]) -> int:
    """How many configured labels appear as standalone strings inside an element."""
    wanted = {label.lower() for label in labels}
    return sum(1 for s in element.find_all(string=True) if _norm_label(s) in wanted)


def _norm_label(text: str) -> str:
    """Label text without whitespace noise or a trailing colon, lower-cased."""
    return re.sub(r"\s+", " ", str(text)).strip().rstrip(":").strip().lower()


def _label_value(soup: BeautifulSoup, label: str) -> str:
    """Value for a label: the label element's following siblings, else the next string."""
    target = label.lower()
    all_labels = load_ingestion().html_labels
    for node in soup.find_all(string=lambda s: _norm_label(s) == target):
        value = " ".join(_sibling_texts(node.parent, all_labels))
        if not value:
            value = _next_string(node, target)
        if value:
            return value
    return ""


def _sibling_texts(element: Tag, labels: list[str]) -> list[str]:
    """Texts of the elements after `element`, up to the next labelled field."""
    texts: list[str] = []
    for sibling in element.next_siblings:
        if isinstance(sibling, Tag) and _count_labels(sibling, labels) > 0:
            break
        text = _text(sibling)
        if text:
            texts.append(text)
    return texts


def _next_string(node: NavigableString, label: str) -> str:
    """First non-empty string after `node` that is not the label itself."""
    following = node.find_next(string=lambda s: s.strip() and _norm_label(s) != label)
    return _clean(str(following)) if following else ""


def _pair_fields(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Label/value pairs from <dt>/<dd> and <th>/<td> markup."""
    pairs: list[tuple[str, str]] = []
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            pairs.append((_text(dt), _text(dd)))
    for tr in soup.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th is not None and td is not None:
            pairs.append((_text(th), _text(td)))
    return [(k.rstrip(":").strip(), v) for k, v in pairs if k and v]


def _text(element: Tag | NavigableString) -> str:
    """Visible text of an element with whitespace collapsed."""
    if isinstance(element, NavigableString):
        return _clean(str(element)) if type(element) is NavigableString else ""
    return _clean(element.get_text(" "))


def _clean(text: str) -> str:
    """Collapse whitespace."""
    return re.sub(r"\s+", " ", text).strip()
