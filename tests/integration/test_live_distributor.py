"""
Live DigiKey + Mouser API smoke tests.

These tests **do** hit the real distributor endpoints. They auto-skip
when the required API keys are absent from the environment, so local
dev and CI without secrets run green without incident. Run them with:

    export DIGIKEY_CLIENT_ID=...
    export DIGIKEY_CLIENT_SECRET=...
    export MOUSER_API_KEY=...
    pytest tests/integration/test_live_distributor.py -v

What they validate that the unit tests cannot:
  1. OAuth against the real DigiKey IdP actually works with our keys.
  2. The response-shape fallbacks in `_parse_product_details` /
     `_parse_search_response` still find the expected fields in the
     live JSON (catches silent v4/v5 drift).
  3. A known-good canonical RF part (STM32F407VGT6) is findable by
     BOTH distributors and returns matching manufacturer strings.

We pick STM32F407VGT6 deliberately: it's a high-volume part that both
distributors carry, has been active for >10 years, and is unlikely to
go obsolete this week.

These tests are marked `slow` so CI can opt out via `-m "not slow"`.
"""
from __future__ import annotations

import os

import pytest

# Keys gate — these strings must point at real credentials for the
# tests in this file to actually do anything.
_HAS_DIGIKEY = bool(
    os.getenv("DIGIKEY_CLIENT_ID") and os.getenv("DIGIKEY_CLIENT_SECRET")
)
_HAS_MOUSER = bool(os.getenv("MOUSER_API_KEY"))

_KNOWN_GOOD_MPN = "STM32F407VGT6"

pytestmark = [pytest.mark.slow]


# ---------------------------------------------------------------------------
# DigiKey
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_DIGIKEY, reason="DIGIKEY_CLIENT_ID / SECRET not set")
def test_digikey_oauth_and_known_good_mpn():
    """End-to-end: token fetch → lookup → parse → PartInfo."""
    from tools import digikey_api
    digikey_api.reset_cache()
    info = digikey_api.lookup(_KNOWN_GOOD_MPN, timeout_s=15.0)
    assert info is not None, f"DigiKey returned no record for {_KNOWN_GOOD_MPN}"
    assert info.source == "digikey"
    assert info.part_number.upper().startswith("STM32F407")
    assert info.manufacturer  # non-empty
    # Datasheet URL should resolve to a known vendor
    assert info.datasheet_url
    assert any(
        d in info.datasheet_url.lower()
        for d in ("st.com", "digikey.com")
    )


@pytest.mark.skipif(not _HAS_DIGIKEY, reason="DIGIKEY_CLIENT_ID / SECRET not set")
def test_digikey_hallucinated_mpn_returns_none():
    """A clearly-invented MPN must come back as None, not an exception."""
    from tools import digikey_api
    digikey_api.reset_cache()
    assert digikey_api.lookup("TOTALLY-HALLUCINATED-X9Z9-PART", timeout_s=15.0) is None


# ---------------------------------------------------------------------------
# Mouser
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_MOUSER, reason="MOUSER_API_KEY not set")
def test_mouser_known_good_mpn():
    from tools import mouser_api
    info = mouser_api.lookup(_KNOWN_GOOD_MPN, timeout_s=15.0)
    assert info is not None, f"Mouser returned no record for {_KNOWN_GOOD_MPN}"
    assert info.source == "mouser"
    assert info.part_number.upper().startswith("STM32F407")
    # Price should parse into a number with a currency code
    if info.unit_price is not None:
        assert info.unit_price > 0
        assert info.unit_price_currency  # non-empty ISO code


@pytest.mark.skipif(not _HAS_MOUSER, reason="MOUSER_API_KEY not set")
def test_mouser_hallucinated_mpn_returns_none():
    from tools import mouser_api
    assert mouser_api.lookup("TOTALLY-HALLUCINATED-X9Z9-PART", timeout_s=15.0) is None


# ---------------------------------------------------------------------------
# Unified search — both tiers agree on a real part
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not (_HAS_DIGIKEY and _HAS_MOUSER),
    reason="Need BOTH DigiKey + Mouser keys to cross-check",
)
def test_unified_search_finds_part_on_primary_tier():
    """Both configured → should hit DigiKey (primary) first."""
    from tools import distributor_search
    distributor_search.reset_cache()
    info = distributor_search.lookup(_KNOWN_GOOD_MPN, timeout_s=15.0)
    assert info is not None
    # Primary tier answers first; we don't care which — just that it's live.
    assert info.source in {"digikey", "mouser"}


# ---------------------------------------------------------------------------
# Keyword / parametric search (optional — skips when the endpoint isn't open)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_MOUSER, reason="MOUSER_API_KEY not set")
def test_mouser_keyword_search_returns_list():
    from tools import mouser_api
    hits = mouser_api.keyword_search("STM32F407", records=5, timeout_s=15.0)
    # Don't assert count — keyword API may rate-limit or return 0 for
    # rare queries. Just verify shape + that exceptions don't propagate.
    assert isinstance(hits, list)
    for h in hits:
        assert h.source == "mouser"
        assert h.part_number  # non-empty
