"""Load and validate evaluation data: retrieval questions and gold field answers.

Matching rules shared by every evaluator live here too, so retrieval and
extraction scoring use the same normalisation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

from rfp_intel.config import ROOT, field_names

EVAL_DIR = ROOT / "eval"
QUESTIONS_PATH = EVAL_DIR / "retrieval_questions.jsonl"
GOLD_DIR = EVAL_DIR / "gold"


class RetrievalQuestion(BaseModel):
    """One retrieval eval question (BUILD_PLAN 7.1)."""

    id: int
    question: str
    bid_id: str
    expected_file: str
    key_phrase: str


class GoldField(BaseModel):
    """Gold answer for one field (BUILD_PLAN 7.2)."""

    value: str | list[str] | None
    must_contain: list[str] = []
    status: Literal["ok", "not_found"] = "ok"
    scoring: Literal["exact", "faithfulness"] = "exact"
    notes: str = ""

    @model_validator(mode="after")
    def _not_found_has_no_value(self) -> GoldField:
        """A not_found gold answer must have value null and no tokens."""
        if self.status == "not_found" and (self.value is not None or self.must_contain):
            raise ValueError("not_found gold fields need value null and empty must_contain")
        return self


_NOISE = re.compile(r"[\s\-‐-―/_]+")   # whitespace, hyphens, en/em dashes, slashes, underscores


def normalize(text: str) -> str:
    """Case-, whitespace- and hyphen-insensitive form used for all phrase matching.

    "white-glove", "white glove" and "whiteglove" (likewise "W-9" / "W9",
    "Contract_Affidavit.pdf" / "Contract Affidavit") are the same token; every
    other character is kept so numbers and ids still have to match exactly.
    """
    return _NOISE.sub("", text).lower()


def contains_phrase(text: str, phrase: str) -> bool:
    """True if `phrase` occurs in `text`, ignoring case, whitespace and hyphens."""
    return normalize(phrase) in normalize(text)


def is_relevant(file_name: str, chunk_text: str, question: RetrievalQuestion) -> bool:
    """A hit is relevant if it comes from the expected file and contains the key phrase."""
    return file_name == question.expected_file and contains_phrase(chunk_text, question.key_phrase)


def load_retrieval_questions(path: Path = QUESTIONS_PATH) -> list[RetrievalQuestion]:
    """All retrieval questions, in file order."""
    with open(path, encoding="utf-8") as fh:
        return [RetrievalQuestion.model_validate_json(line) for line in fh if line.strip()]


def load_gold(bid_id: str, gold_dir: Path = GOLD_DIR) -> dict[str, GoldField]:
    """Gold answers for one bid; must cover exactly the configured fields."""
    with open(gold_dir / f"{bid_id}.json", encoding="utf-8") as fh:
        raw = json.load(fh)
    expected, got = set(field_names()), set(raw)
    if got != expected:
        raise ValueError(
            f"gold/{bid_id}.json fields mismatch: missing={sorted(expected - got)} "
            f"unexpected={sorted(got - expected)}"
        )
    return {name: GoldField(**raw[name]) for name in field_names()}


def gold_bid_ids(gold_dir: Path = GOLD_DIR) -> list[str]:
    """Bid ids that have a gold file."""
    return sorted(path.stem for path in gold_dir.glob("*.json"))
