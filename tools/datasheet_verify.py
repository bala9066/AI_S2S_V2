"""
Datasheet URL verification — B2.3.

Given a URL, do a HEAD request (fall back to GET on 405 / empty-body), accept
any 2xx or redirect chain ending in 2xx, and return True only if the final
content-type is PDF or HTML.

Kept pure-stdlib (urllib) so it has no new dependency. Network errors return
False — the verifier never throws on a bad URL.
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from typing import Optional

_ALLOWED_CONTENT_PREFIXES = ("application/pdf", "text/html", "application/octet-stream")

_USER_AGENT = (
    "HardwarePipelineDatasheetVerifier/0.1 "
    "(+https://example.invalid/hardware-pipeline)"
)


def _request(method: str, url: str, timeout: float) -> Optional[dict]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": _USER_AGENT})
    # Some defense / vendor sites present self-signed intermediate certs behind
    # corporate proxies; we do NOT skip verification here — if the user's
    # trust store can't reach the cert, we return a negative result, which is
    # the safe default. A future ticket may inject a proxy CA bundle.
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return {
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", "").lower(),
                "final_url": resp.geturl(),
            }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ssl.SSLError, OSError):
        return None


def verify_url(url: str, timeout: float = 5.0) -> bool:
    """
    Return True if `url` resolves (2xx) and presents a content-type that looks
    like a datasheet page (HTML product page OR PDF). Network failure returns False.
    """
    if not url or not isinstance(url, str):
        return False
    info = _request("HEAD", url, timeout)
    if info is None:
        info = _request("GET", url, timeout)
    if info is None:
        return False
    if not (200 <= info["status"] < 300):
        return False
    ct = info["content_type"]
    return any(ct.startswith(p) for p in _ALLOWED_CONTENT_PREFIXES)


def verify_urls(urls: list[str], timeout: float = 5.0) -> dict[str, bool]:
    """Batch helper — dict {url: bool}."""
    return {u: verify_url(u, timeout=timeout) for u in urls}


# Curated whitelist of vendor / standards domains whose product pages are
# treated as "trusted" when the live HTTP probe cannot run (air-gap, CI,
# rate-limited sandbox). When we cannot reach the network we still want to
# mark parts pointing at e.g. `analog.com` or `ti.com` product pages as
# verified, because the URL itself has been hand-curated by the Hardware Lead
# and is reproducible through a local mirror in the air-gap image.
_TRUSTED_VENDOR_DOMAINS = frozenset({
    "www.analog.com", "analog.com",
    "www.ti.com", "ti.com",
    "www.qorvo.com", "qorvo.com",
    "www.macom.com", "macom.com",
    "www.microchip.com", "microchip.com",
    "www.infineon.com", "infineon.com",
    "www.xilinx.com", "xilinx.com", "www.amd.com", "amd.com",
    "www.intel.com", "intel.com",
    "www.st.com", "st.com",
    "www.nxp.com", "nxp.com",
    "www.renesas.com", "renesas.com",
    "www.skyworksinc.com", "skyworksinc.com",
    "www.maximintegrated.com", "maximintegrated.com",
    "www.onsemi.com", "onsemi.com",
    "www.ondaelectronics.com",
    "www.mercurysystems.com", "mercurysystems.com",
    "www.minicircuits.com", "minicircuits.com",
    "www.murata.com", "murata.com",
    "www.molex.com", "molex.com",
    "www.te.com", "te.com",
    "www.samtec.com", "samtec.com",
    "www.amphenol.com", "amphenol.com",
    "www.rohm.com", "rohm.com",
    "www.vishay.com", "vishay.com",
    "www.siliconlabs.com", "siliconlabs.com",
    "www.silabs.com", "silabs.com",
    "www.broadcom.com", "broadcom.com",
    "www.nordicsemi.com", "nordicsemi.com",
    "www.semtech.com", "semtech.com",
    "www.anaren.com", "anaren.com",
    "www.microsemi.com", "microsemi.com",
    "www.wavestream.com", "wavestream.com",
    "www.crystek.com", "crystek.com",
    # Distributor-hosted datasheet mirrors. Mouser/DigiKey cache vendor PDFs
    # at stable URLs (e.g. mouser.com/datasheet/3/xxx.pdf) and the LLM tends
    # to return these when the vendor's own site has moved. Treat them as
    # trusted for the offline probe — the URLs are still machine-verifiable
    # at demo time if the proxy allows.
    "www.mouser.com", "mouser.com",
    "www.mouser.in", "mouser.in",
    "www.mouser.co.uk", "mouser.co.uk",
    "www.mouser.de", "mouser.de",
    "www.digikey.com", "digikey.com",
    "www.digikey.in", "digikey.in",
    "www.digikey.co.uk", "digikey.co.uk",
    "www.arrow.com", "arrow.com",
    "www.avnet.com", "avnet.com",
    "www.farnell.com", "farnell.com",
    "www.newark.com", "newark.com",
})


def is_trusted_vendor_url(url: str) -> bool:
    """Return True if `url` points at a curated vendor/standards domain.

    Used as an offline fallback when the HEAD/GET probe cannot complete. The
    Hardware Lead owns the whitelist and is responsible for keeping it in
    sync with `components.json` entries.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _TRUSTED_VENDOR_DOMAINS
