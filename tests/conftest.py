"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from tests.fixtures.make_mini_bid import MINI_BID_DIR, build


@pytest.fixture(scope="session")
def mini_bid() -> Path:
    """The synthetic mini bid folder (generated if missing)."""
    expected = ("rfp.pdf", "notice_update.pdf", "bid_page.html")
    if not all((MINI_BID_DIR / name).exists() for name in expected):
        build(MINI_BID_DIR)
    return MINI_BID_DIR
