"""Tests for tools/mouser_api.py. Network stubbed."""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from tools.mouser_api import is_configured, lookup


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("MOUSER_API_KEY", "test-mouser-key")
    monkeypatch.setenv("MOUSER_API_URL", "https://api.mouser.com/api/v2")


def _mock_urlopen(payload):
    class _Ctx:
        def __init__(self, data):
            self._data = data
        def read(self):
            return self._data if isinstance(self._data, bytes) else self._data.encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _open(*_a, **_k):
        if isinstance(payload, Exception):
            raise payload
        return _Ctx(payload)

    return patch("tools.mouser_api.urllib.request.urlopen", side_effect=_open)


def test_not_configured_when_env_missing(monkeypatch):
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert is_configured() is False
    assert lookup("ADL8107") is None


def test_successful_lookup_returns_partinfo(configured):
    body = json.dumps({
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [{
                "ManufacturerPartNumber": "ADL8107",
                "Manufacturer": "Analog Devices Inc.",
                "Description": "Wideband LNA 2-18 GHz",
                "DataSheetUrl": "https://www.analog.com/media/adl8107.pdf",
                "ProductDetailUrl": "https://www.mouser.com/...",
                "LifecycleStatus": "Active",
                "AvailabilityInStock": "180",
                "PriceBreaks": [
                    {"Quantity": 1, "Price": "$24.50", "Currency": "USD"},
                ],
            }],
        }
    })
    with _mock_urlopen(body):
        info = lookup("ADL8107")
    assert info is not None
    assert info.part_number == "ADL8107"
    assert info.manufacturer == "Analog Devices Inc."
    assert info.lifecycle_status == "active"
    assert info.unit_price_usd == 24.5
    assert info.stock_quantity == 180
    assert info.source == "mouser"


def test_no_results_returns_none(configured):
    body = json.dumps({"SearchResults": {"NumberOfResult": 0, "Parts": []}})
    with _mock_urlopen(body):
        assert lookup("HALLUCINATED-XYZ") is None


def test_prefers_exact_mpn_match_over_fuzzy_candidates(configured):
    body = json.dumps({
        "SearchResults": {
            "NumberOfResult": 2,
            "Parts": [
                {"ManufacturerPartNumber": "ADL8107-EVAL",
                 "Manufacturer": "ADI", "LifecycleStatus": "Active"},
                {"ManufacturerPartNumber": "ADL8107",
                 "Manufacturer": "ADI", "LifecycleStatus": "Active"},
            ],
        }
    })
    with _mock_urlopen(body):
        info = lookup("ADL8107")
    assert info is not None
    assert info.part_number == "ADL8107"


def test_multiple_fuzzy_matches_return_none(configured):
    """No exact match + multiple candidates → decline to guess."""
    body = json.dumps({
        "SearchResults": {
            "NumberOfResult": 2,
            "Parts": [
                {"ManufacturerPartNumber": "X-1", "Manufacturer": "A"},
                {"ManufacturerPartNumber": "X-2", "Manufacturer": "A"},
            ],
        }
    })
    with _mock_urlopen(body):
        assert lookup("X") is None


@pytest.mark.parametrize("raw,expected_lifecycle", [
    ("Active", "active"),
    ("In Production", "active"),
    ("", "active"),
    ("Not Recommended for New Designs", "nrnd"),
    ("End of Life", "nrnd"),
    ("Obsolete", "obsolete"),
    ("Something Else", "unknown"),
])
def test_lifecycle_mapping(configured, raw, expected_lifecycle):
    body = json.dumps({
        "SearchResults": {
            "NumberOfResult": 1,
            "Parts": [{
                "ManufacturerPartNumber": "Y",
                "Manufacturer": "Vendor",
                "LifecycleStatus": raw,
            }]
        }
    })
    with _mock_urlopen(body):
        info = lookup("Y")
    assert info is not None
    assert info.lifecycle_status == expected_lifecycle


def test_http_404_returns_none(configured):
    err = urllib.error.HTTPError("url", 404, "not found", {}, io.BytesIO(b""))
    with _mock_urlopen(err):
        assert lookup("X") is None


def test_network_error_returns_none(configured):
    with _mock_urlopen(urllib.error.URLError("network down")):
        assert lookup("X") is None


def test_malformed_json_returns_none(configured):
    with _mock_urlopen("not json"):
        assert lookup("X") is None
