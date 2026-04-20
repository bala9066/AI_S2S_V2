"""Tests for tools/digikey_api.py.

Network is stubbed via mock_open on urllib.request.urlopen — we never
hit the real DigiKey API.
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from tools import digikey_api
from tools.digikey_api import PartInfo, is_configured, lookup, reset_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "test-client")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("DIGIKEY_API_URL", "https://api.digikey.com/v3")


def _mock_urlopen(*side_effects):
    """Patch urlopen to return a sequence of `bytes | Exception` values.
    Each call pops the next value — bytes become HTTP-200 bodies.
    """
    call_iter = iter(side_effects)

    class _Ctx:
        def __init__(self, payload):
            self._payload = payload
        def read(self):
            return self._payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _open(*_a, **_k):
        nxt = next(call_iter)
        if isinstance(nxt, Exception):
            raise nxt
        return _Ctx(nxt if isinstance(nxt, bytes) else nxt.encode("utf-8"))

    return patch("tools.digikey_api.urllib.request.urlopen", side_effect=_open)


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------

def test_not_configured_when_env_missing(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    assert is_configured() is False


def test_configured_when_both_keys_set(configured):
    assert is_configured() is True


def test_lookup_returns_none_when_not_configured(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    assert lookup("ADL8107") is None


# ---------------------------------------------------------------------------
# OAuth token fetch
# ---------------------------------------------------------------------------

def test_token_fetch_caches_and_reuses(configured):
    token_resp = json.dumps({"access_token": "tok-123", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "ADL8107",
            "Manufacturer": {"Value": "Analog Devices"},
            "ProductDescription": "Wideband LNA 2-18 GHz",
            "ProductStatus": {"Status": "Active"},
            "PrimaryDatasheet": "https://www.analog.com/en/products/adl8107.html",
            "ProductUrl": "https://www.digikey.com/en/products/detail/ADL8107",
            "UnitPrice": 24.0,
            "QuantityAvailable": 125,
        }
    })
    # One token call + two lookups → still only one token request
    # because of the in-process cache.
    with _mock_urlopen(token_resp, product_resp, product_resp) as mock_open_fn:
        info1 = lookup("ADL8107")
        info2 = lookup("ADL8107")
    assert mock_open_fn.call_count == 3  # 1 token + 2 lookup calls
    assert info1 is not None and info2 is not None
    assert info1.part_number == "ADL8107"
    assert info1.manufacturer == "Analog Devices"
    assert info1.lifecycle_status == "active"
    assert info1.source == "digikey"


def test_token_fetch_failure_returns_none(configured):
    with _mock_urlopen(urllib.error.URLError("connection refused")):
        assert lookup("ADL8107") is None


def test_token_fetch_missing_access_token_returns_none(configured):
    bad = json.dumps({"token_type": "Bearer"})  # no access_token
    with _mock_urlopen(bad):
        assert lookup("ADL8107") is None


# ---------------------------------------------------------------------------
# Lookup outcomes
# ---------------------------------------------------------------------------

def test_lookup_404_returns_none(configured):
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    http_404 = urllib.error.HTTPError(
        "url", 404, "not found", {}, io.BytesIO(b"")
    )
    with _mock_urlopen(token_resp, http_404):
        assert lookup("HALLUCINATED-MPN") is None


def test_lookup_401_resets_token_cache(configured):
    token_resp = json.dumps({"access_token": "tok-old", "expires_in": 3600})
    http_401 = urllib.error.HTTPError(
        "url", 401, "unauthorised", {}, io.BytesIO(b"")
    )
    with _mock_urlopen(token_resp, http_401):
        assert lookup("ADL8107") is None
    # Cache cleared → next call tries to fetch a new token.
    assert digikey_api._cached_token["access_token"] is None


@pytest.mark.parametrize("status,expected", [
    ("Active", "active"),
    ("Active / Preferred", "active"),
    ("Last Time Buy", "nrnd"),
    ("Not Recommended for New Designs", "nrnd"),
    ("Obsolete Available", "nrnd"),
    ("Discontinued", "obsolete"),
    ("", "unknown"),
])
def test_lifecycle_status_mapping(configured, status, expected):
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    product_resp = json.dumps({
        "ProductDetails": {
            "ManufacturerPartNumber": "X",
            "Manufacturer": {"Value": "Y"},
            "ProductStatus": {"Status": status},
        }
    })
    with _mock_urlopen(token_resp, product_resp):
        info = lookup("X")
    assert info is not None
    assert info.lifecycle_status == expected


def test_unexpected_response_shape_returns_none(configured):
    token_resp = json.dumps({"access_token": "tok", "expires_in": 3600})
    garbage = json.dumps(["not", "a", "dict"])
    with _mock_urlopen(token_resp, garbage):
        assert lookup("X") is None


def test_empty_part_number_returns_none(configured):
    assert lookup("") is None
    assert lookup(None) is None  # type: ignore[arg-type]
