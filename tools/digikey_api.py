"""
DigiKey ProductSearch API client — closes the component-hallucination gap.

The requirements_agent can invent a part number ("HMC8999LP4E") and ship
it with a fabricated datasheet URL. Before this module landed, nothing
in the pipeline actually asked DigiKey whether that MPN exists. Now
`lookup(part_number)` hits DigiKey's ProductSearch v3 API and returns
a `PartInfo` on success or `None` on miss — which `services/rf_audit`
translates into a `hallucinated_part` AuditIssue so the user sees the
invention instead of trusting it.

Auth: OAuth2 client-credentials — needs DIGIKEY_CLIENT_ID +
DIGIKEY_CLIENT_SECRET in .env. When the keys are missing, every lookup
returns None and logs once at INFO level (NOT air-gap fail: the rest of
the pipeline keeps running; the part is then validated against the
local seed + Mouser fallback).

Tokens are cached in-process until expiry + 60 s jitter so we don't
re-authenticate every call.

No external deps — stdlib urllib only.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartInfo:
    """Normalised response from a distributor lookup. Matches the shape
    `rf_audit` expects so DigiKey / Mouser / seed can all be fused."""
    part_number: str
    manufacturer: str
    description: str
    datasheet_url: Optional[str]
    product_url: Optional[str]
    lifecycle_status: str        # "active" | "nrnd" | "obsolete" | "unknown"
    unit_price_usd: Optional[float]
    stock_quantity: Optional[int]
    source: str                  # "digikey" | "mouser" | "seed" | "chromadb"

    def to_dict(self) -> dict:
        return {
            "part_number": self.part_number,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "datasheet_url": self.datasheet_url,
            "product_url": self.product_url,
            "lifecycle_status": self.lifecycle_status,
            "unit_price_usd": self.unit_price_usd,
            "stock_quantity": self.stock_quantity,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Token cache (process-local)
# ---------------------------------------------------------------------------

_token_lock = threading.Lock()
_cached_token: dict = {"access_token": None, "expires_at": 0.0}


def _client_config() -> tuple[str, str, str]:
    """Return (client_id, client_secret, api_base). Empty strings when
    the env vars are missing — callers must handle that case."""
    client_id = os.getenv("DIGIKEY_CLIENT_ID", "").strip()
    client_secret = os.getenv("DIGIKEY_CLIENT_SECRET", "").strip()
    api_base = os.getenv("DIGIKEY_API_URL", "https://api.digikey.com/v3").rstrip("/")
    return client_id, client_secret, api_base


def is_configured() -> bool:
    cid, cs, _ = _client_config()
    return bool(cid and cs)


def reset_cache() -> None:
    """Clear the in-process token cache — test-helper."""
    with _token_lock:
        _cached_token["access_token"] = None
        _cached_token["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# OAuth2 client-credentials flow
# ---------------------------------------------------------------------------

def _fetch_token(*, timeout_s: float = 8.0) -> Optional[str]:
    """Request a fresh access token. Returns None on any failure so the
    caller can fall through to Mouser / seed lookups."""
    client_id, client_secret, api_base = _client_config()
    if not (client_id and client_secret):
        return None
    token_url = f"{api_base}/oauth2/token"
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("ascii")
    req = urllib.request.Request(
        token_url, data=body, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "HardwarePipeline/2.0 (+digikey_api.py)",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ssl.SSLError, OSError, json.JSONDecodeError) as exc:
        log.info("digikey.token_fetch_failed: %s", exc)
        return None

    access = payload.get("access_token")
    expires_in = int(payload.get("expires_in") or 0)
    if not access:
        log.warning("digikey.token_missing_access_token")
        return None
    with _token_lock:
        _cached_token["access_token"] = access
        # Expire 60 s early so we never use a just-about-to-expire token.
        _cached_token["expires_at"] = time.time() + max(60, expires_in - 60)
    log.info("digikey.token_refreshed expires_in=%d", expires_in)
    return access


def _get_token() -> Optional[str]:
    """Return a cached token when valid, else request a new one."""
    with _token_lock:
        if (
            _cached_token["access_token"]
            and _cached_token["expires_at"] > time.time()
        ):
            return _cached_token["access_token"]
    return _fetch_token()


# ---------------------------------------------------------------------------
# Part lookup
# ---------------------------------------------------------------------------

def lookup(part_number: str, *, timeout_s: float = 6.0) -> Optional[PartInfo]:
    """Search DigiKey for a manufacturer part number.

    Returns:
      PartInfo when DigiKey recognises the MPN.
      None when: API not configured, auth failed, HTTP error, MPN unknown.

    This is the pipeline's "is this MPN real?" oracle. Do NOT raise —
    callers rely on a sentinel None to trigger the next-tier fallback.
    """
    if not part_number:
        return None
    if not is_configured():
        return None

    token = _get_token()
    if not token:
        return None

    client_id, _, api_base = _client_config()
    url = f"{api_base}/products/search/{urllib.parse.quote(part_number)}/productdetails"
    req = urllib.request.Request(
        url, method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "X-DIGIKEY-Client-Id": client_id,
            "X-DIGIKEY-Locale-Site":      "US",
            "X-DIGIKEY-Locale-Language":  "en",
            "X-DIGIKEY-Locale-Currency":  "USD",
            "Accept": "application/json",
            "User-Agent": "HardwarePipeline/2.0 (+digikey_api.py)",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 404 => part not in catalog (not a bug — the expected "invented MPN" path).
        if getattr(exc, "code", None) == 404:
            log.debug("digikey.mpn_not_found: %s", part_number)
            return None
        # 401 -> token stale; wipe the cache so the next call retries once.
        if getattr(exc, "code", None) == 401:
            reset_cache()
        log.info("digikey.http_error %s: %s", getattr(exc, "code", "?"), exc)
        return None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError,
            OSError, json.JSONDecodeError) as exc:
        log.info("digikey.lookup_failed pn=%s: %s", part_number, exc)
        return None

    return _parse_product_details(payload, part_number)


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

# DigiKey ProductStatus codes we consider "ship-safe".
_ACTIVE_PRODUCT_STATUSES = {"Active", "Active / Preferred"}
_NRND_PRODUCT_STATUSES = {"Last Time Buy", "Obsolete Available", "Not Recommended for New Designs"}


def _parse_product_details(payload: dict, requested_pn: str) -> Optional[PartInfo]:
    """Translate DigiKey's JSON shape into a PartInfo.

    DigiKey returns a wrapper {"ProductDetails": {...}} — be defensive
    because the API has mutated over v2→v3→v4 without breaking the outer
    shape. If the shape is unexpected, log and fall through to None.
    """
    if not isinstance(payload, dict):
        return None
    details = (
        payload.get("ProductDetails")
        or payload.get("Product")
        or payload
    )
    if not isinstance(details, dict):
        return None

    mfr_info = details.get("Manufacturer") or {}
    manufacturer = (
        mfr_info.get("Value")
        or mfr_info.get("Name")
        or details.get("ManufacturerName")
        or ""
    )
    mfr_pn = (
        details.get("ManufacturerPartNumber")
        or details.get("ManufacturerProductNumber")
        or requested_pn
    )
    description = (
        details.get("ProductDescription")
        or details.get("DetailedDescription")
        or ""
    )
    datasheet_url = details.get("PrimaryDatasheet") or details.get("DatasheetUrl")
    product_url = details.get("ProductUrl") or details.get("ProductPath")

    # Lifecycle status
    status_raw = (details.get("ProductStatus") or {})
    if isinstance(status_raw, dict):
        status_text = status_raw.get("Status") or status_raw.get("Value") or ""
    else:
        status_text = str(status_raw)
    if status_text in _ACTIVE_PRODUCT_STATUSES:
        lifecycle = "active"
    elif status_text in _NRND_PRODUCT_STATUSES:
        lifecycle = "nrnd"
    elif status_text:
        lifecycle = "obsolete"
    else:
        lifecycle = "unknown"

    price = None
    try:
        up = details.get("UnitPrice")
        if up is not None:
            price = float(up)
    except (TypeError, ValueError):
        price = None

    stock = None
    try:
        q = details.get("QuantityAvailable") or details.get("Quantity")
        if q is not None:
            stock = int(q)
    except (TypeError, ValueError):
        stock = None

    return PartInfo(
        part_number=str(mfr_pn).strip(),
        manufacturer=str(manufacturer).strip(),
        description=str(description).strip(),
        datasheet_url=str(datasheet_url).strip() if datasheet_url else None,
        product_url=str(product_url).strip() if product_url else None,
        lifecycle_status=lifecycle,
        unit_price_usd=price,
        stock_quantity=stock,
        source="digikey",
    )
