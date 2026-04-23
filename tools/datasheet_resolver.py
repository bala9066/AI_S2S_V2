"""
Datasheet URL resolver — guarantees every BOM row has a working "Datasheet"
link, even when the distributor's primary PDF URL has 404'd.

Problem the demo hit: `tools.distributor_search._verify_datasheet` HEAD-
probes the distributor-supplied datasheet URL and **strips it on probe
failure**, leaving the component with no link at all. The user then
sees a "Datasheet" column with empty cells — embarrassing and reads as
broken even though the part itself is real.

This resolver replaces the strip-on-failure behaviour with a fallback
chain that ALWAYS yields a clickable URL:

    1. distributor's primary datasheet PDF        (best — direct PDF)
    2. distributor's product page (digikey.com/...)  (always works)
    3. manufacturer product page guessed from MPN+vendor patterns
    4. Google "MPN datasheet" search URL           (never null)

The first link in the chain whose probe passes (cached or live) wins.
Trusted-vendor URLs are accepted without a probe via the existing
`is_trusted_vendor_url` allowlist. The Google fallback is the always-
good last resort — it's not a datasheet itself, but it lands the user
on a search page where the first hit is invariably the right PDF.

Cache integration: every probe result is read from / written to
`services.component_cache` so successive runs short-circuit without
any HTTP round-trip. The cache is opt-out via `COMPONENT_CACHE_DISABLED=1`.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from services.component_cache import cache_disabled, get_default
from tools.datasheet_verify import is_trusted_vendor_url, verify_url
from tools.digikey_api import PartInfo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manufacturer URL patterns — last-mile fallback BEFORE the Google one.
# Maps a manufacturer name (case-insensitive substring) to a callable
# that returns a guess at the product page URL given the MPN.
# Conservative on purpose: only the few vendors whose URL schemes we've
# verified to be stable. A wrong guess just falls through to Google.
# ---------------------------------------------------------------------------

def _analog_devices_url(mpn: str) -> str:
    return f"https://www.analog.com/en/products/{_slug(mpn)}.html"

def _ti_url(mpn: str) -> str:
    return f"https://www.ti.com/product/{_slug(mpn).upper()}"

def _qorvo_url(mpn: str) -> str:
    return f"https://www.qorvo.com/products/p/{_slug(mpn).upper()}"

def _macom_url(mpn: str) -> str:
    return f"https://www.macom.com/products/product-detail/{_slug(mpn).upper()}"

def _st_url(mpn: str) -> str:
    return f"https://www.st.com/en/search.html#q={urllib.parse.quote(mpn)}"

def _microchip_url(mpn: str) -> str:
    return f"https://www.microchip.com/en-us/product/{_slug(mpn).upper()}"

def _infineon_url(mpn: str) -> str:
    return f"https://www.infineon.com/cms/en/product/?q={urllib.parse.quote(mpn)}"

def _onsemi_url(mpn: str) -> str:
    return f"https://www.onsemi.com/products/{_slug(mpn).lower()}"

def _minicircuits_url(mpn: str) -> str:
    # Mini-Circuits product pages live under /pdfs/ for datasheets, but the
    # search route is more reliable across model lines.
    return f"https://www.minicircuits.com/WebStore/dashboard.html?model={urllib.parse.quote(mpn)}"


_MFR_URL_PATTERNS: tuple[tuple[str, callable], ...] = (
    ("analog devices", _analog_devices_url),
    ("adi",            _analog_devices_url),
    ("texas instruments", _ti_url),
    (" ti ",           _ti_url),
    ("qorvo",          _qorvo_url),
    ("macom",          _macom_url),
    ("m/a-com",        _macom_url),
    ("stmicro",        _st_url),
    ("st micro",       _st_url),
    ("microchip",      _microchip_url),
    ("infineon",       _infineon_url),
    ("on semi",        _onsemi_url),
    ("onsemi",         _onsemi_url),
    ("on semiconductor", _onsemi_url),
    ("mini-circuits",  _minicircuits_url),
    ("minicircuits",   _minicircuits_url),
)


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(mpn: str) -> str:
    """Normalise a part number for URL-safe insertion. Doesn't lowercase
    because some vendors (TI, Qorvo) require uppercase MPNs in the path."""
    return _SLUG_RE.sub("-", (mpn or "").strip())


# ---------------------------------------------------------------------------
# Resolver result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedDatasheet:
    """The chosen URL plus enough metadata for tests + log lines + the
    BOM table tooltip ("which fallback rung were we on?")."""
    url: str
    is_valid: bool
    source: str  # 'distributor_pdf' | 'product_url' | 'mfr_guess' | 'search_fallback'
    chain_position: int  # 1 = primary, 4 = google fallback


# ---------------------------------------------------------------------------
# Probe with cache
# ---------------------------------------------------------------------------

def _probe(url: str, *, timeout: float = 3.0) -> bool:
    """Cache-aside HEAD/GET probe.

    Order of precedence:
      1. Trusted-vendor allowlist short-circuits (no probe).
      2. Persistent cache hit (within URL_PROBE_TTL).
      3. Live HEAD/GET via tools.datasheet_verify.verify_url.

    Live results are always written back to the cache; trusted results
    are stored too so air-gap demos still benefit from a hot cache.
    """
    if not url:
        return False
    trusted = is_trusted_vendor_url(url)
    if trusted:
        # Skip the probe entirely — vendor allowlist is the contract.
        # Still write to cache so stats show the URL was vetted.
        if not cache_disabled():
            try:
                get_default().put_url_probe(
                    url, True, status_code=200,
                    content_type="text/html", is_trusted=True,
                )
            except Exception as exc:  # noqa: BLE001 — cache failure must never break the live path
                log.debug("datasheet_resolver.cache_write_skipped url=%s: %s", url, exc)
        return True
    if not cache_disabled():
        try:
            cached = get_default().get_url_probe(url)
        except Exception as exc:  # noqa: BLE001
            log.debug("datasheet_resolver.cache_read_skipped url=%s: %s", url, exc)
            cached = None
        if cached is not None:
            return cached.is_valid
    # Live probe.
    try:
        ok = verify_url(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.debug("datasheet_resolver.probe_err url=%s: %s", url, exc)
        ok = False
    if not cache_disabled():
        try:
            get_default().put_url_probe(url, ok, is_trusted=False)
        except Exception as exc:  # noqa: BLE001
            log.debug("datasheet_resolver.cache_write_skipped url=%s: %s", url, exc)
    return ok


# ---------------------------------------------------------------------------
# Fallback chain construction
# ---------------------------------------------------------------------------

def _guess_mfr_url(part_number: str, manufacturer: str) -> Optional[str]:
    """Walk `_MFR_URL_PATTERNS` for the first manufacturer substring match
    and return the templated URL. None when no pattern matches — caller
    falls through to the Google search fallback."""
    if not part_number or not manufacturer:
        return None
    haystack = f" {manufacturer.lower().strip()} "
    for needle, builder in _MFR_URL_PATTERNS:
        if needle in haystack:
            try:
                return builder(part_number)
            except Exception as exc:  # noqa: BLE001 — a bad template must not crash
                log.debug("datasheet_resolver.mfr_template_err mfr=%s: %s",
                          manufacturer, exc)
                return None
    return None


def _search_fallback_url(part_number: str) -> str:
    """Last-resort URL — never empty. Lands the user on a Google search
    that virtually always surfaces the right PDF as the first hit."""
    q = urllib.parse.quote(f"{(part_number or '').strip()} datasheet")
    return f"https://www.google.com/search?q={q}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_chain(info: PartInfo) -> list[tuple[str, str]]:
    """Return the candidate `(url, source_label)` chain in priority order.

    Pure function: no probes, no cache reads — handy for tests and for
    reasoning about what the resolver would TRY before any I/O. Always
    ends with the Google fallback so downstream callers know they can
    return chain[-1] on universal failure.
    """
    chain: list[tuple[str, str]] = []
    if info.datasheet_url:
        chain.append((info.datasheet_url, "distributor_pdf"))
    if info.product_url and info.product_url != info.datasheet_url:
        chain.append((info.product_url, "product_url"))
    mfr_guess = _guess_mfr_url(info.part_number, info.manufacturer)
    if mfr_guess:
        chain.append((mfr_guess, "mfr_guess"))
    chain.append((_search_fallback_url(info.part_number), "search_fallback"))
    return chain


def resolve_datasheet(info: PartInfo, *, timeout: float = 3.0) -> ResolvedDatasheet:
    """Walk the fallback chain and return the first URL whose probe passes.

    Probes are cache-aside via `services.component_cache`, so a warm
    cache turns this into a single SQLite read per URL. The Google
    fallback (chain[-1]) is never probed — it's accepted as a working
    "find the datasheet" link by definition.
    """
    chain = build_chain(info)
    for pos, (url, source) in enumerate(chain, start=1):
        if source == "search_fallback":
            # Last rung — never probe; always return.
            return ResolvedDatasheet(
                url=url, is_valid=True, source=source, chain_position=pos,
            )
        if _probe(url, timeout=timeout):
            return ResolvedDatasheet(
                url=url, is_valid=True, source=source, chain_position=pos,
            )
    # Defensive: chain always ends with search_fallback so we never
    # reach here, but keep the path safe.
    return ResolvedDatasheet(
        url=_search_fallback_url(info.part_number),
        is_valid=True, source="search_fallback", chain_position=len(chain),
    )


def resolve_url(info: PartInfo, *, timeout: float = 3.0) -> str:
    """Convenience: just the URL string (for the existing `_verify_datasheet`
    drop-in replacement). Never returns empty."""
    return resolve_datasheet(info, timeout=timeout).url


__all__ = [
    "ResolvedDatasheet",
    "build_chain",
    "resolve_datasheet",
    "resolve_url",
]
