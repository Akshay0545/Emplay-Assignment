"""Process-wide services the graph nodes use (search engine, prompt loading).

The engine is created lazily and shared, because Qdrant local mode allows one
client per storage folder. Tests inject fakes with `set_engine`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from rfp_intel.config import settings

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_engine = None


def get_engine():
    """The shared SearchEngine (created on first use)."""
    global _engine
    if _engine is None:
        from rfp_intel.search.engine import SearchEngine

        _engine = SearchEngine()
    return _engine


def set_engine(engine) -> None:
    """Replace the shared engine (tests, API lifespan)."""
    global _engine
    _engine = engine


def close_engine() -> None:
    """Close and forget the shared engine."""
    global _engine
    if _engine is not None:
        _engine.close()
        _engine = None


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """A prompt template from agents/prompts/<name>.md."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def bid_folder(bid_id: str) -> Path:
    """Folder of a bid under data_dir."""
    return settings.path("data_dir") / bid_id
