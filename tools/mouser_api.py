"""
Mouser Search API client — second-tier distributor lookup.

DigiKey is the primary, Mouser is the fallback when DigiKey doesn't
know the MPN (their catalogues overlap but differ at the edges). Same
`PartInfo` contract so callers don't care which distributor answered.

Auth: single API key (MOUSER_API_KEY) passed as `?apikey=`. No OAuth.
Docs: https://www.mouser.com/api-search/
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from tools.digikey_api import PartInfo

log = logging.getLogger(__name__)


def _config() -> tuple[str, str]:
    api_key = os.getenv("MOUSER_API_KEY", "").strip()
    api_base = os.getenv("MOUSER_API_URL", "https://api.mouser.com/api/v2").rstrip("/")
    return api_key, api_base


def is_configured() -> bool:
    return bool(_config()[0])


# ---------------------------------------------------------------------------

def lookup(part_number: str, *, timeout_s: float = 6.0) -> Optional[PartInfo]:
    """Search Mouser for a manufacturer part number.

    Returns a PartInfo on match, None otherwise (API not configured /
    HTTP error / not-found / unexpected shape).
    """
    if not part_number:
        return None
    api_key, api_base = _config()
    if not api_key:
        return None

    url = f"{api_base}/search/partnumber?apiKey={urllib.parse.quote(api_key)}"
    body = json.dumps({
        "SearchByPartRequest": {
            "mouserPartNumber": part_number,
            "partSearchOptions": "string",
        }
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "HardwarePipeline/2.0 (+mouser_api.py)",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if getattr(exc, "code", None) == 404:
            return None
        log.info("mouser.http_error %s: %s", getattr(exc, "code", "?"), exc)
        return None
    except (urllib.error.URLError, TimeoutError, ssl.SSLError,
            OSError, json.JSONDecodeError) as exc:
        log.info("mouser.lookup_failed pn=%s: %s", part_number, exc)
        return None

    return _parse_search_response(payload, part_number)


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

# Mouser uses "LifecycleStatus" string directly.
_ACTIVE_LIFECYCLES = {"Active", "In Production", ""}
_NRND_LIFECYCLES = {"Not Recommended for New Designs", "NRND",
                    "Last Time Buy", "End of Life"}
_OBSOLETE_LIFECYCLES = {"Obsolete", "Discontinued"}


def _parse_search_response(payload: dict, requested_pn: str) -> Optional[PartInfo]:
    # Shape: {"SearchResults": {"NumberOfResult": N, "Parts": [{...}, ...]}}
    results = (payload or {}).get("SearchResults") or {}
    parts = results.get("Parts") or []
    if not parts:
        return None

    requested_norm = requested_pn.strip().lower()
    best = None
    for p in parts:
        mfr_pn = (p.get("ManufacturerPartNumber") or "").strip()
        if mfr_pn.lower() == requested_norm:
            best = p
            break
    if best is None:
        # Mouser often returns fuzzy matches — only accept them if no
        # strong equality match was found AND only one candidate exists.
        if len(parts) == 1:
            best = parts[0]
        else:
            return None

    status_text = (best.get("LifecycleStatus") or "").strip()
    if status_text in _ACTIVE_LIFECYCLES:
        lifecycle = "active"
    elif status_text in _NRND_LIFECYCLES:
        lifecycle = "nrnd"
    elif status_text in _OBSOLETE_LIFECYCLES:
        lifecycle = "obsolete"
    else:
        lifecycle = "unknown"

    price = _first_price_break_usd(best.get("PriceBreaks") or [])

    try:
        stock = int(best.get("AvailabilityInStock") or 0)
    except (TypeError, ValueError):
        stock = None

    return PartInfo(
        part_number=(best.get("ManufacturerPartNumber") or requested_pn).strip(),
        manufacturer=(best.get("Manufacturer") or "").strip(),
        description=(best.get("Description") or "").strip(),
        datasheet_url=(best.get("DataSheetUrl") or "").strip() or None,
        product_url=(best.get("ProductDetailUrl") or "").strip() or None,
        lifecycle_status=lifecycle,
        unit_price_usd=price,
        stock_quantity=stock,
        source="mouser",
    )


def _first_price_break_usd(breaks: list) -> Optional[float]:
    """Mouser returns per-quantity price breaks with locale strings
    (`"$4.58"`). Pick the 1-off price, strip the currency symbol."""
    if not breaks:
        return None
    first = breaks[0] or {}
    raw = (first.get("Price") or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace(",", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return None
