"""Tests for `tools.datasheet_resolver` — the fallback chain that
guarantees every BOM row gets a clickable datasheet link.

Coverage matrix:

  * `_slug`               — URL-safe normalisation
  * `_search_fallback_url` — never empty, properly URL-encoded
  * `_guess_mfr_url`       — manufacturer pattern matching, miss returns None
  * `build_chain`          — order, dedup, always ends with search_fallback
  * `_probe`               — trusted short-circuit, cache hit, cache miss → live → write
  * `resolve_datasheet`    — first probe pass wins, falls all the way through,
                              never returns is_valid=False
  * `resolve_url`          — convenience wrapper

The cache singleton is redirected at a temp DB per test so we never
touch the shipped `data/component_cache.db`. `verify_url` is patched
where the resolver imports it (module-local symbol) so we exercise the
chain in isolation from any HTTP code.
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest

from services import component_cache as cc
from tools import datasheet_resolver as dr
from tools.datasheet_resolver import (
    ResolvedDatasheet,
    _guess_mfr_url,
    _probe,
    _search_fallback_url,
    _slug,
    build_chain,
    resolve_datasheet,
    resolve_url,
)
from tools.digikey_api import PartInfo


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_cache(tmp_path: Path, monkeypatch):
    """Point the singleton at a fresh temp DB so cache writes from the
    resolver don't pollute the shipped cache file. Reset after."""
    db = tmp_path / "resolver_cache.db"
    monkeypatch.setenv("COMPONENT_CACHE_PATH", str(db))
    monkeypatch.delenv("COMPONENT_CACHE_DISABLED", raising=False)
    cc.reset_default()
    yield cc.get_default()
    cc.reset_default()


@pytest.fixture
def cache_off(monkeypatch):
    """Disable the persistent cache so we exercise the strictly-live
    code path (used to prove the resolver still works without the RAG
    layer in place)."""
    monkeypatch.setenv("COMPONENT_CACHE_DISABLED", "1")
    cc.reset_default()
    yield
    cc.reset_default()


def _part(
    pn: str = "ADL8107",
    mfr: str = "Analog Devices Inc.",
    datasheet_url: str | None = "https://www.analog.com/media/en/datasheet/adl8107.pdf",
    product_url: str | None = "https://www.analog.com/en/products/ADL8107.html",
) -> PartInfo:
    return PartInfo(
        part_number=pn,
        manufacturer=mfr,
        description="Wideband LNA 2-18 GHz",
        datasheet_url=datasheet_url,
        product_url=product_url,
        lifecycle_status="active",
        unit_price_usd=24.0,
        stock_quantity=180,
        source="digikey",
    )


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------

class TestSlug:
    def test_passes_through_safe_chars(self):
        assert _slug("ADL8107") == "ADL8107"
        assert _slug("HMC-8410") == "HMC-8410"
        assert _slug("LM3.3-1.5") == "LM3.3-1.5"

    def test_replaces_unsafe_chars(self):
        # Spaces, slashes, weird punctuation collapse to a single dash.
        assert _slug("ADL 8107") == "ADL-8107"
        assert _slug("ADL/8107") == "ADL-8107"
        assert _slug("ADL  8107") == "ADL-8107"

    def test_strips_leading_trailing_whitespace(self):
        assert _slug("  ADL8107  ") == "ADL8107"

    def test_empty_returns_empty(self):
        assert _slug("") == ""
        assert _slug(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _search_fallback_url
# ---------------------------------------------------------------------------

class TestSearchFallbackUrl:
    def test_returns_google_search_url(self):
        url = _search_fallback_url("ADL8107")
        assert url.startswith("https://www.google.com/search?q=")
        assert "ADL8107" in url
        assert "datasheet" in url

    def test_url_encodes_special_chars(self):
        url = _search_fallback_url("HMC 8410 / IF")
        # Spaces / slashes must be percent-encoded — never raw in the URL.
        assert " " not in url
        assert "/IF" not in url  # the "/" inside the MPN gets escaped
        # Decoding round-trips back to the original (with " datasheet" suffix).
        # Strip the prefix to extract just the q param.
        q = url.split("q=", 1)[1]
        assert urllib.parse.unquote(q) == "HMC 8410 / IF datasheet"

    def test_empty_part_number_still_returns_a_url(self):
        # Even with no MPN we still return a working Google search page —
        # the contract is "never null".
        url = _search_fallback_url("")
        assert url.startswith("https://www.google.com/search?q=")
        assert "datasheet" in url


# ---------------------------------------------------------------------------
# _guess_mfr_url
# ---------------------------------------------------------------------------

class TestGuessMfrUrl:
    def test_analog_devices_full_name(self):
        # _slug doesn't lowercase — preserves MPN case for path correctness.
        url = _guess_mfr_url("ADL8107", "Analog Devices Inc.")
        assert url == "https://www.analog.com/en/products/ADL8107.html"

    def test_analog_devices_adi_alias(self):
        url = _guess_mfr_url("ADL8107", "ADI")
        assert url == "https://www.analog.com/en/products/ADL8107.html"

    def test_texas_instruments(self):
        url = _guess_mfr_url("LM5145", "Texas Instruments")
        assert url == "https://www.ti.com/product/LM5145"

    def test_qorvo_uppercases_mpn(self):
        url = _guess_mfr_url("qpa9120", "Qorvo")
        assert url == "https://www.qorvo.com/products/p/QPA9120"

    def test_macom(self):
        url = _guess_mfr_url("MAAM-011106", "MACOM Technology Solutions")
        assert "macom.com" in url
        assert "MAAM-011106" in url

    def test_macom_via_legacy_alias(self):
        # "M/A-COM" is the historical name, still appears in distributor data.
        url = _guess_mfr_url("MAAM-011106", "M/A-COM")
        assert "macom.com" in url

    def test_stmicro(self):
        url = _guess_mfr_url("STM32F407", "STMicroelectronics")
        assert "st.com" in url
        assert "STM32F407" in url

    def test_microchip(self):
        url = _guess_mfr_url("PIC18F4550", "Microchip Technology")
        assert "microchip.com" in url
        assert "PIC18F4550" in url

    def test_infineon(self):
        url = _guess_mfr_url("BCR401U", "Infineon Technologies")
        assert "infineon.com" in url
        assert "BCR401U" in url

    def test_onsemi_canonical(self):
        url = _guess_mfr_url("NCP1117", "onsemi")
        assert "onsemi.com" in url

    def test_onsemi_legacy_full_name(self):
        url = _guess_mfr_url("NCP1117", "ON Semiconductor")
        assert "onsemi.com" in url

    def test_minicircuits(self):
        url = _guess_mfr_url("ZHL-42W+", "Mini-Circuits")
        assert "minicircuits.com" in url
        assert "ZHL-42W" in url

    def test_unknown_manufacturer_returns_none(self):
        assert _guess_mfr_url("X1234", "ObscureCo Ltd") is None

    def test_empty_mpn_returns_none(self):
        assert _guess_mfr_url("", "Analog Devices") is None

    def test_empty_manufacturer_returns_none(self):
        assert _guess_mfr_url("ADL8107", "") is None

    def test_case_insensitive_match(self):
        url = _guess_mfr_url("ADL8107", "ANALOG DEVICES INC")
        assert url is not None
        assert "analog.com" in url


# ---------------------------------------------------------------------------
# build_chain (pure function, no I/O)
# ---------------------------------------------------------------------------

class TestBuildChain:
    def test_full_chain_includes_all_four_rungs(self):
        chain = build_chain(_part())
        # 1: distributor PDF, 2: product page, 3: mfr guess, 4: search fallback.
        assert len(chain) == 4
        sources = [src for _, src in chain]
        assert sources == ["distributor_pdf", "product_url", "mfr_guess", "search_fallback"]

    def test_chain_always_ends_with_search_fallback(self):
        # Even a totally bare PartInfo (no datasheet, no product url, unknown mfr)
        # still gets a valid Google fallback as the last rung.
        bare = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        chain = build_chain(bare)
        assert chain[-1][1] == "search_fallback"
        assert chain[-1][0].startswith("https://www.google.com/search?q=")

    def test_skips_missing_distributor_pdf(self):
        info = _part(datasheet_url=None)
        sources = [src for _, src in build_chain(info)]
        assert "distributor_pdf" not in sources
        assert sources[0] == "product_url"

    def test_skips_missing_product_url(self):
        info = _part(product_url=None)
        sources = [src for _, src in build_chain(info)]
        assert "product_url" not in sources
        assert sources[0] == "distributor_pdf"

    def test_dedupes_when_product_url_equals_datasheet_url(self):
        same = "https://www.analog.com/en/products/adl8107.html"
        info = _part(datasheet_url=same, product_url=same)
        sources = [src for _, src in build_chain(info)]
        # product_url is dropped because it equals the datasheet rung.
        assert sources.count("distributor_pdf") == 1
        assert "product_url" not in sources

    def test_skips_mfr_guess_for_unknown_manufacturer(self):
        info = _part(mfr="ObscureCo")
        sources = [src for _, src in build_chain(info)]
        assert "mfr_guess" not in sources
        # Still ends with search fallback.
        assert sources[-1] == "search_fallback"

    def test_minimal_chain_is_just_search_fallback(self):
        info = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        chain = build_chain(info)
        assert len(chain) == 1
        assert chain[0][1] == "search_fallback"

    def test_pure_function_no_side_effects(self):
        # build_chain must not touch the network or the cache. We prove
        # this by patching both and asserting they were never called.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify, \
             patch("tools.datasheet_resolver.get_default") as mock_cache:
            chain = build_chain(_part())
            assert len(chain) == 4
            mock_verify.assert_not_called()
            mock_cache.assert_not_called()


# ---------------------------------------------------------------------------
# _probe
# ---------------------------------------------------------------------------

class TestProbe:
    def test_empty_url_returns_false(self, temp_cache):
        assert _probe("") is False

    def test_trusted_vendor_short_circuits_no_live_probe(self, temp_cache):
        # Trusted vendor = no HEAD/GET. We prove this by patching verify_url
        # and asserting it was never called.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify:
            ok = _probe("https://www.analog.com/en/products/adl8107.html")
        assert ok is True
        mock_verify.assert_not_called()
        # And the trusted result was written to the cache for later reads.
        hit = temp_cache.get_url_probe("https://www.analog.com/en/products/adl8107.html")
        assert hit is not None
        assert hit.is_valid is True

    def test_cache_hit_short_circuits_live_probe(self, temp_cache):
        url = "https://random-distributor.invalid/foo.pdf"
        # Pre-populate the cache so the next _probe should not hit verify_url.
        temp_cache.put_url_probe(url, True, status_code=200,
                                 content_type="application/pdf",
                                 is_trusted=False)
        with patch("tools.datasheet_resolver.verify_url") as mock_verify:
            ok = _probe(url)
        assert ok is True
        mock_verify.assert_not_called()

    def test_cache_miss_calls_verify_and_writes_back(self, temp_cache):
        url = "https://random-distributor.invalid/never-cached.pdf"
        # Cache is empty; verify_url should be called and the result cached.
        with patch("tools.datasheet_resolver.verify_url",
                   return_value=True) as mock_verify:
            ok = _probe(url)
        assert ok is True
        mock_verify.assert_called_once()
        # Subsequent probe must read from cache, not from verify_url.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify2:
            ok2 = _probe(url)
        assert ok2 is True
        mock_verify2.assert_not_called()

    def test_negative_probe_is_cached(self, temp_cache):
        url = "https://broken-link.invalid/404.pdf"
        with patch("tools.datasheet_resolver.verify_url",
                   return_value=False) as mock_verify:
            ok = _probe(url)
        assert ok is False
        mock_verify.assert_called_once()
        # Negative result is cached so we don't re-probe the dead URL.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify2:
            ok2 = _probe(url)
        assert ok2 is False
        mock_verify2.assert_not_called()

    def test_verify_url_exception_returns_false_safely(self, temp_cache):
        # If the underlying verifier crashes the resolver must return
        # False, not propagate.
        with patch("tools.datasheet_resolver.verify_url",
                   side_effect=RuntimeError("boom")):
            ok = _probe("https://something.invalid/x.pdf")
        assert ok is False

    def test_cache_disabled_falls_through_to_live_probe(self, cache_off):
        url = "https://random-distributor.invalid/x.pdf"
        with patch("tools.datasheet_resolver.verify_url",
                   return_value=True) as mock_verify:
            ok = _probe(url)
        assert ok is True
        mock_verify.assert_called_once()


# ---------------------------------------------------------------------------
# resolve_datasheet
# ---------------------------------------------------------------------------

class TestResolveDatasheet:
    def test_returns_distributor_pdf_when_it_probes_ok(self, temp_cache):
        # Patch the trusted check so we exercise the live-probe branch
        # rather than the trusted short-circuit (analog.com is in the
        # allowlist by default).
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=True):
            result = resolve_datasheet(_part())
        assert isinstance(result, ResolvedDatasheet)
        assert result.url == "https://www.analog.com/media/en/datasheet/adl8107.pdf"
        assert result.source == "distributor_pdf"
        assert result.chain_position == 1
        assert result.is_valid is True

    def test_falls_through_to_product_url_when_pdf_probe_fails(self, temp_cache):
        # First chain entry probes False, second probes True. Mirror the
        # default _part() URLs exactly (incl. MPN case in product page).
        responses = {"https://www.analog.com/media/en/datasheet/adl8107.pdf": False,
                     "https://www.analog.com/en/products/ADL8107.html": True}
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   side_effect=lambda u, timeout=3.0: responses.get(u, False)):
            result = resolve_datasheet(_part())
        assert result.source == "product_url"
        assert result.chain_position == 2
        assert result.url == "https://www.analog.com/en/products/ADL8107.html"

    def test_falls_through_to_mfr_guess_when_distributor_links_dead(self, temp_cache):
        # Both distributor links 404; only the mfr guess passes.
        info = _part(
            datasheet_url="https://distributor.invalid/dead.pdf",
            product_url="https://distributor.invalid/dead.html",
        )
        # _analog_devices_url preserves MPN case — see _slug docstring.
        guess = "https://www.analog.com/en/products/ADL8107.html"

        def fake_verify(url, timeout=3.0):
            return url == guess

        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   side_effect=fake_verify):
            result = resolve_datasheet(info)
        assert result.source == "mfr_guess"
        assert result.chain_position == 3
        assert result.url == guess

    def test_falls_all_the_way_to_search_fallback_when_nothing_probes(self, temp_cache):
        # Nothing in the chain passes a probe. Resolver MUST return the
        # Google fallback, not give up.
        info = _part(
            datasheet_url="https://distributor.invalid/dead.pdf",
            product_url="https://distributor.invalid/dead.html",
            mfr="ObscureCo Ltd",  # no mfr_guess pattern matches
        )
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=False):
            result = resolve_datasheet(info)
        assert result.source == "search_fallback"
        assert result.url.startswith("https://www.google.com/search?q=")
        assert "ADL8107" in result.url
        # is_valid is True even for the fallback — it's a working link
        # by definition (Google never 404s on a search query).
        assert result.is_valid is True

    def test_search_fallback_is_never_probed(self, temp_cache):
        """Even if every other probe fails AND we'd love to verify it,
        the Google URL is the contractual last-resort. Make sure we
        don't probe it (it'd waste a request and pollute the cache)."""
        info = _part(
            datasheet_url=None, product_url=None, mfr="ObscureCo",
        )
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url") as mock_verify:
            result = resolve_datasheet(info)
        assert result.source == "search_fallback"
        mock_verify.assert_not_called()

    def test_trusted_vendor_url_returned_without_live_probe(self, temp_cache):
        # analog.com is in the trusted allowlist — distributor PDF should
        # win on the first rung without any HEAD/GET.
        with patch("tools.datasheet_resolver.verify_url") as mock_verify:
            result = resolve_datasheet(_part())
        assert result.source == "distributor_pdf"
        assert result.chain_position == 1
        mock_verify.assert_not_called()

    def test_never_returns_empty_url(self, temp_cache):
        # No matter how broken the input, the URL must be non-empty.
        bare = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=False):
            result = resolve_datasheet(bare)
        assert result.url
        assert result.url.strip()


# ---------------------------------------------------------------------------
# resolve_url (convenience wrapper)
# ---------------------------------------------------------------------------

class TestResolveUrl:
    def test_returns_just_the_url_string(self, temp_cache):
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=True):
            url = resolve_url(_part())
        assert isinstance(url, str)
        assert url == "https://www.analog.com/media/en/datasheet/adl8107.pdf"

    def test_never_empty_even_on_total_failure(self, temp_cache):
        bare = _part(datasheet_url=None, product_url=None, mfr="ObscureCo")
        with patch("tools.datasheet_resolver.is_trusted_vendor_url",
                   return_value=False), \
             patch("tools.datasheet_resolver.verify_url",
                   return_value=False):
            url = resolve_url(bare)
        assert url
        assert url.startswith("https://www.google.com/search?q=")
