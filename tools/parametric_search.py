"""
Parametric component retrieval — closes the "LLM invents part numbers" gap.

The LLM used to pick components from its training knowledge and the audit
would only *retroactively* catch inventions (hallucinated_part blockers).
This tool flips the flow:

  stage + spec hint  -->  live distributor query  -->  real candidate list  -->  LLM picks one

The LLM never needs to invent an MPN; it selects from a shortlist of real,
in-stock parts returned by DigiKey + Mouser.

Public API:
    candidates = find_candidates("LNA", "2-18 GHz low noise wideband")
    for c in candidates:
        print(c.part_number, c.manufacturer, c.datasheet_url)

Results are de-duplicated by MPN (upper-cased), obsolete parts are dropped,
and the list is capped so the LLM's context stays bounded.

Performance:
  * DigiKey and Mouser are queried **in parallel** (ThreadPoolExecutor) —
    total latency is max(DigiKey, Mouser), not sum.
  * Successful results are cached in-process with a 60-second TTL keyed
    on (stage, hint, max_per_source, drop_obsolete). A typical P1 flow
    makes 7-10 retrieval calls over ~30s; the cache absorbs repeated
    stages (e.g. LNA queried twice with the same hint) and cuts the
    wall-clock cost of the second query to ~0.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional

from tools import digikey_api, mouser_api
from tools.digikey_api import PartInfo

log = logging.getLogger(__name__)


# Canonical RF-chain stages → default keyword boost. Callers pass extra
# spec context; this map seeds the query so the right distributor
# category is matched even when the caller's hint is sparse.
_STAGE_KEYWORDS: dict[str, str] = {
    "lna":         "low noise amplifier LNA",
    "driver_amp":  "driver amplifier RF",
    "gain_block":  "gain block RF amplifier",
    "pa":          "RF power amplifier",
    "mixer":       "RF mixer double balanced",
    "limiter":     "RF limiter PIN diode",
    "bpf":         "bandpass filter RF",
    "lpf":         "lowpass filter RF",
    "hpf":         "highpass filter RF",
    "preselector": "ceramic bandpass filter RF preselector",
    "saw":         "SAW filter RF",
    "splitter":    "power splitter combiner RF",
    "balun":       "balun transformer RF",
    "attenuator":  "RF attenuator step",
    "switch":      "RF switch SPDT SP4T",
    "vco":         "VCO voltage controlled oscillator",
    "pll":         "PLL synthesiser RF",
    "adc":         "analog to digital converter",
    "dac":         "digital to analog converter",
    "fpga":        "FPGA",
    "mcu":         "microcontroller ARM Cortex",
    "ldo":         "LDO voltage regulator",
    "buck":        "buck DC-DC converter",
    "bias_tee":    "bias tee RF",
    "tcxo":        "TCXO temperature compensated oscillator",
    "ocxo":        "OCXO oven controlled oscillator",
}


# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

# TTL is intentionally short: distributor stock/lifecycle can change within
# an hour, and a P1 run completes in minutes. 60s is long enough to absorb
# duplicate calls within a single run, short enough that re-running a
# project after a pause gets fresh data.
_CACHE_TTL_S = 60.0

_cache_lock = threading.Lock()
_cache: dict[tuple, tuple[float, list[PartInfo]]] = {}


def reset_cache() -> None:
    """Clear the retrieval cache. Test helper; also exposed for callers
    that want to force a fresh pull after mutating env vars."""
    with _cache_lock:
        _cache.clear()


def _cache_get(key: tuple) -> Optional[list[PartInfo]]:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        ts, value = entry
        if now - ts > _CACHE_TTL_S:
            # Stale — evict so repeated misses don't grow the dict.
            _cache.pop(key, None)
            return None
        # Return a shallow copy so callers can mutate without affecting
        # future cache hits. PartInfo is frozen, so list() is enough.
        return list(value)


def _cache_put(key: tuple, value: list[PartInfo]) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), list(value))


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def _normalise_stage(stage: str) -> str:
    return (stage or "").strip().lower().replace("-", "_").replace(" ", "_")


def _build_query(stage: str, hint: str) -> str:
    """Compose the keyword query sent to each distributor.

    Uses the stage's canonical keyword seed when we know the stage,
    otherwise falls back to the stage string itself. The caller's
    `hint` (e.g. `"2-18 GHz NF < 2 dB"`) is appended so distributor
    search engines can score on the spec constraints.
    """
    seed = _STAGE_KEYWORDS.get(_normalise_stage(stage), stage)
    parts = [seed.strip(), (hint or "").strip()]
    return " ".join(p for p in parts if p)


def _is_obsolete(info: PartInfo) -> bool:
    return info.lifecycle_status == "obsolete"


def _dedupe_by_mpn(infos: Iterable[PartInfo]) -> list[PartInfo]:
    """Keep the first occurrence of each MPN (case-insensitive).

    Order matters: callers pass DigiKey results before Mouser so DigiKey
    wins on overlap — DigiKey exposes a structured `ProductStatus`
    whereas Mouser's `LifecycleStatus` is occasionally `None`, and the
    structured status drives lifecycle filtering downstream.
    """
    seen: set[str] = set()
    out: list[PartInfo] = []
    for info in infos:
        key = (info.part_number or "").strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(info)
    return out


# ---------------------------------------------------------------------------
# Parallel fetchers
# ---------------------------------------------------------------------------

def _fetch_digikey(query: str, max_per_source: int, timeout_s: float) -> list[PartInfo]:
    if not digikey_api.is_configured():
        return []
    try:
        return digikey_api.keyword_search(
            query, limit=max_per_source, timeout_s=timeout_s,
        )
    except Exception as exc:
        log.warning("parametric_search.digikey_failed q=%r: %s", query, exc)
        return []


def _fetch_mouser(query: str, max_per_source: int, timeout_s: float) -> list[PartInfo]:
    if not mouser_api.is_configured():
        return []
    try:
        return mouser_api.keyword_search(
            query, records=max_per_source, timeout_s=timeout_s,
        )
    except Exception as exc:
        log.warning("parametric_search.mouser_failed q=%r: %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_candidates(
    stage: str,
    hint: str = "",
    *,
    max_per_source: int = 5,
    max_total: Optional[int] = None,
    drop_obsolete: bool = True,
    timeout_s: float = 10.0,
) -> list[PartInfo]:
    """Return a merged, de-duplicated candidate list from DigiKey + Mouser.

    Args:
        stage:             Canonical stage id (e.g. "lna", "mixer", "adc")
                           OR any free-text if the stage is unknown.
        hint:              Extra spec context (frequency range, NF target,
                           package, etc.) appended to the distributor query.
        max_per_source:    Upper bound on results fetched from each API.
        max_total:         Cap on the final merged list (default =
                           2 * max_per_source).
        drop_obsolete:     When True (default), parts with lifecycle
                           "obsolete" are removed from the result.
        timeout_s:         Per-call HTTP timeout.

    Returns:
        List of PartInfo, DigiKey hits first then Mouser, deduped.
        Empty list when both distributors fail or nothing matches.
    """
    query = _build_query(stage, hint)
    if not query:
        return []

    # Cache key normalises the inputs so "LNA" + "2-18 GHz" hits the same
    # bucket as "  lna " + "2-18 GHz ". We include max_per_source /
    # drop_obsolete because they materially affect the cached list.
    cache_key = (
        _normalise_stage(stage),
        (hint or "").strip().lower(),
        int(max_per_source),
        bool(drop_obsolete),
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        log.debug("parametric_search.cache_hit q=%r", query)
        if max_total is None:
            max_total = 2 * max_per_source
        return cached[:max_total]

    # Parallel fetch — total latency drops to max(DigiKey, Mouser). Two
    # workers is enough; we never call more than two APIs here.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="parametric-search") as pool:
        dk_future = pool.submit(_fetch_digikey, query, max_per_source, timeout_s)
        ms_future = pool.submit(_fetch_mouser,  query, max_per_source, timeout_s)
        dk = dk_future.result()
        ms = ms_future.result()

    merged = _dedupe_by_mpn(dk + ms)
    if drop_obsolete:
        merged = [p for p in merged if not _is_obsolete(p)]

    # Cache the merged+filtered list *before* applying max_total — that
    # lets a subsequent call with a different max_total still reuse the
    # same underlying shortlist.
    _cache_put(cache_key, merged)

    if max_total is None:
        max_total = 2 * max_per_source
    return merged[:max_total]


# Disable cache entirely when the caller sets this env var — useful for
# the `scripts/demo_parametric_search.py` smoke test and for ops who
# want to force live traffic in debugging.
if os.getenv("PARAMETRIC_SEARCH_DISABLE_CACHE", "").strip() in {"1", "true", "yes"}:
    _CACHE_TTL_S = 0.0
