"""
Unified part-number lookup: DigiKey → Mouser → local seed.

`services/rf_audit` uses this to decide whether a part number the LLM
emitted is real. Order matters:

  1. **DigiKey** — broadest catalogue, authoritative MPN database.
  2. **Mouser** — fallback; catches parts DigiKey drops or hasn't added yet.
  3. **Local seed** (`data/sample_components.json`) — air-gap / offline
     demo path; always populated from curated entries.

Missing everywhere → the MPN is treated as **hallucinated** and an
audit issue is raised.

Opt-outs:
  SKIP_DISTRIBUTOR_LOOKUP=1  → never hit the network; use seed only.
  SKIP_DIGIKEY=1             → skip DigiKey, still try Mouser.
  SKIP_MOUSER=1              → skip Mouser.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from tools.digikey_api import PartInfo
from tools import digikey_api, mouser_api

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SEED_PATH = _REPO_ROOT / "data" / "sample_components.json"

# Process-local lookup cache — avoids hammering the APIs for a BOM
# where the same MPN appears on multiple edges (e.g. power rail fans
# out to 10 ICs, all with the same decoupling cap).
_cache_lock = threading.Lock()
_cache: dict[str, Optional[PartInfo]] = {}
_seed_index: Optional[dict[str, dict]] = None


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

def _skip_all_network() -> bool:
    return os.getenv("SKIP_DISTRIBUTOR_LOOKUP", "").strip() in {"1", "true", "yes"}


def _skip_digikey() -> bool:
    return os.getenv("SKIP_DIGIKEY", "").strip() in {"1", "true", "yes"}


def _skip_mouser() -> bool:
    return os.getenv("SKIP_MOUSER", "").strip() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Seed-JSON fallback
# ---------------------------------------------------------------------------

def _load_seed_index() -> dict[str, dict]:
    """Lazy-load the seed JSON into a {part_number_upper: entry} dict."""
    global _seed_index
    if _seed_index is not None:
        return _seed_index
    idx: dict[str, dict] = {}
    try:
        data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        for entry in data.get("components", []):
            pn = (entry.get("part_number") or "").strip().upper()
            if pn:
                idx[pn] = entry
    except Exception as exc:
        log.warning("distributor_search.seed_load_failed: %s", exc)
    _seed_index = idx
    return idx


def _seed_lookup(part_number: str) -> Optional[PartInfo]:
    pn = (part_number or "").strip().upper()
    if not pn:
        return None
    entry = _load_seed_index().get(pn)
    if not entry:
        return None
    status = (entry.get("lifecycle_status") or "unknown").lower()
    return PartInfo(
        part_number=entry.get("part_number", part_number),
        manufacturer=entry.get("manufacturer", ""),
        description=entry.get("description", ""),
        datasheet_url=entry.get("datasheet_url"),
        product_url=None,
        lifecycle_status=status,
        unit_price_usd=entry.get("estimated_cost_usd"),
        stock_quantity=None,
        source="seed",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reset_cache() -> None:
    """Clear the process-local lookup cache + seed index. Test helper."""
    global _seed_index
    with _cache_lock:
        _cache.clear()
    _seed_index = None
    digikey_api.reset_cache()


def lookup(part_number: str, *, timeout_s: float = 6.0) -> Optional[PartInfo]:
    """Return a `PartInfo` for `part_number`, consulting in order:
    DigiKey, Mouser, local seed. None when every tier misses."""
    if not part_number:
        return None
    key = part_number.strip().upper()
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    info: Optional[PartInfo] = None
    skip_net = _skip_all_network()

    if not skip_net and not _skip_digikey() and digikey_api.is_configured():
        info = digikey_api.lookup(part_number, timeout_s=timeout_s)

    if info is None and not skip_net and not _skip_mouser() and mouser_api.is_configured():
        info = mouser_api.lookup(part_number, timeout_s=timeout_s)

    if info is None:
        info = _seed_lookup(part_number)

    with _cache_lock:
        _cache[key] = info
    return info


def batch_lookup(
    part_numbers: list[str], *, timeout_s: float = 6.0,
) -> dict[str, Optional[PartInfo]]:
    """Convenience wrapper — dict {part_number: PartInfo | None}. Serial
    for simplicity; most BOMs are <50 parts and latency dominates network
    round-trip, not CPU."""
    out: dict[str, Optional[PartInfo]] = {}
    for pn in part_numbers:
        out[pn] = lookup(pn, timeout_s=timeout_s)
    return out


def any_api_configured() -> bool:
    """True when at least one distributor can be queried live."""
    return digikey_api.is_configured() or mouser_api.is_configured()
