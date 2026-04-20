"""Tests for tools/parametric_search.py.

Distributor APIs are patched at the `tools.parametric_search` import
surface so the tests never hit the network.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.digikey_api import PartInfo
from tools.parametric_search import (
    _build_query,
    _dedupe_by_mpn,
    _is_obsolete,
    _normalise_stage,
    find_candidates,
)


def _pi(
    pn: str,
    *,
    source: str = "digikey",
    lifecycle: str = "active",
    mfr: str = "Acme",
    ds: str | None = "https://ds/x.pdf",
) -> PartInfo:
    return PartInfo(
        part_number=pn,
        manufacturer=mfr,
        description=f"Part {pn}",
        datasheet_url=ds,
        product_url=f"https://product/{pn}",
        lifecycle_status=lifecycle,
        unit_price_usd=None,
        stock_quantity=100,
        source=source,
    )


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def test_normalise_stage_canonicalises_case_and_separators():
    assert _normalise_stage("LNA") == "lna"
    assert _normalise_stage("  Bias-Tee ") == "bias_tee"
    assert _normalise_stage("ADC ") == "adc"


def test_build_query_seeds_known_stage():
    q = _build_query("lna", "2-18 GHz")
    assert "low noise amplifier" in q.lower()
    assert "2-18 GHz" in q


def test_build_query_falls_back_to_stage_string_when_unknown():
    q = _build_query("magic-widget", "red")
    # Unknown stage is used verbatim as the seed.
    assert "magic-widget" in q
    assert "red" in q


def test_build_query_handles_blank_hint():
    q = _build_query("mixer", "")
    assert "mixer" in q.lower()
    assert q.strip() == q  # no trailing whitespace


# ---------------------------------------------------------------------------
# Deduplication / filtering
# ---------------------------------------------------------------------------

def test_dedupe_keeps_first_occurrence_case_insensitive():
    a = _pi("ADL8107", source="digikey")
    b = _pi("adl8107", source="mouser")  # same MPN, different case
    c = _pi("BGA7210", source="mouser")
    out = _dedupe_by_mpn([a, b, c])
    assert [p.part_number for p in out] == ["ADL8107", "BGA7210"]
    # DigiKey's entry must win on duplicates — drives downstream lifecycle.
    assert out[0].source == "digikey"


def test_dedupe_drops_blank_mpns():
    a = _pi("")
    b = _pi("VALID-1")
    out = _dedupe_by_mpn([a, b])
    assert [p.part_number for p in out] == ["VALID-1"]


def test_is_obsolete():
    assert _is_obsolete(_pi("X", lifecycle="obsolete")) is True
    assert _is_obsolete(_pi("X", lifecycle="active")) is False
    assert _is_obsolete(_pi("X", lifecycle="nrnd")) is False
    assert _is_obsolete(_pi("X", lifecycle="unknown")) is False


# ---------------------------------------------------------------------------
# find_candidates — orchestration
# ---------------------------------------------------------------------------

@pytest.fixture
def both_configured(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "cs")
    monkeypatch.setenv("MOUSER_API_KEY", "mk")


def test_merges_digikey_and_mouser_with_digikey_first(both_configured):
    dk = [_pi("A", source="digikey"), _pi("B", source="digikey")]
    ms = [_pi("C", source="mouser"), _pi("D", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "2-18 GHz")
    assert [p.part_number for p in out] == ["A", "B", "C", "D"]


def test_obsolete_parts_are_dropped_by_default(both_configured):
    dk = [_pi("A-EOL", lifecycle="obsolete"), _pi("A-OK", lifecycle="active")]
    ms = [_pi("B-NRND", lifecycle="nrnd"), _pi("B-ACT", lifecycle="active")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "")
    mpns = [p.part_number for p in out]
    assert "A-EOL" not in mpns, "obsolete parts must be filtered"
    # NRND is kept — still ship-capable; caller can warn separately.
    assert "B-NRND" in mpns
    assert "A-OK" in mpns and "B-ACT" in mpns


def test_can_opt_in_to_obsolete_parts(both_configured):
    dk = [_pi("A-EOL", lifecycle="obsolete")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=[]):
        out = find_candidates("lna", "", drop_obsolete=False)
    assert [p.part_number for p in out] == ["A-EOL"]


def test_max_total_caps_result_list(both_configured):
    dk = [_pi(f"D{i}") for i in range(10)]
    ms = [_pi(f"M{i}") for i in range(10)]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "", max_per_source=5, max_total=6)
    assert len(out) == 6


def test_duplicate_mpn_across_sources_collapsed(both_configured):
    dk = [_pi("SHARED", source="digikey", mfr="MfgA")]
    ms = [_pi("SHARED", source="mouser", mfr="MfgB")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "")
    assert len(out) == 1
    assert out[0].source == "digikey"  # DigiKey wins on overlap


def test_empty_query_returns_empty(both_configured):
    # Blank stage + blank hint → nothing to search for.
    with patch("tools.parametric_search.digikey_api.keyword_search") as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search") as ms_mock:
        out = find_candidates("", "")
    assert out == []
    dk_mock.assert_not_called()
    ms_mock.assert_not_called()


def test_digikey_exception_does_not_break_mouser(both_configured):
    """If DigiKey throws (token expiry, 5xx, etc.) we must still return
    Mouser's results — retrieval must degrade gracefully."""
    ms = [_pi("M1", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search",
               side_effect=RuntimeError("digikey exploded")), \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "2-18 GHz")
    assert [p.part_number for p in out] == ["M1"]


def test_skips_digikey_when_not_configured(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("MOUSER_API_KEY", "mk")
    ms = [_pi("M1", source="mouser")]
    with patch("tools.parametric_search.digikey_api.keyword_search") as dk_mock, \
         patch("tools.parametric_search.mouser_api.keyword_search", return_value=ms):
        out = find_candidates("lna", "")
    dk_mock.assert_not_called()
    assert [p.part_number for p in out] == ["M1"]


def test_skips_mouser_when_not_configured(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "cs")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    dk = [_pi("D1", source="digikey")]
    with patch("tools.parametric_search.digikey_api.keyword_search", return_value=dk), \
         patch("tools.parametric_search.mouser_api.keyword_search") as ms_mock:
        out = find_candidates("lna", "")
    ms_mock.assert_not_called()
    assert [p.part_number for p in out] == ["D1"]
