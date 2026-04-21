"""
RF cascade analysis — Friis NF + gain + IIP3 accumulation.

The P1 requirements agent hands us a signal-chain list with per-stage
NF / gain / IIP3 (where the distributor lookup filled them in). This
module walks that list left-to-right and produces the cumulative cascade
numbers so the UI can draw a stage-by-stage bar chart and so the audit
layer can compare the cumulative result against the system claim.

The math is textbook (Friis 1944 + Razavi 1997):

  NF_total_lin = NF1_lin + (NF2_lin - 1) / G1_lin
                         + (NF3_lin - 1) / (G1_lin * G2_lin) + ...
  G_total_db   = sum(Gi_db)
  1 / IIP3_total_lin = 1 / IIP3_1_lin + G1_lin / IIP3_2_lin
                                       + G1_lin * G2_lin / IIP3_3_lin + ...

All inputs are in dB / dBm; conversions to linear happen internally and
the output is reported back in dB / dBm so it slots straight into the
existing design_parameters shape.

`compute_cascade` is side-effect free and returns a JSON-safe dict ready
for `json.dumps`.
"""
from __future__ import annotations

import math
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Stage extraction
# ---------------------------------------------------------------------------

# Keys we'll probe on each component to dig out the RF figures. The LLM
# sometimes writes nf_db, sometimes noise_figure_db; distributors return
# noise_figure; etc. Normalise all of them.
_NF_KEYS = ("nf_db", "noise_figure_db", "noise_figure", "nf")
_GAIN_KEYS = ("gain_db", "gain", "conversion_gain_db", "conversion_gain")
_IIP3_KEYS = ("iip3_dbm", "iip3", "input_ip3_dbm", "oip3_dbm")
_LOSS_KEYS = ("insertion_loss_db", "loss_db", "il_db", "insertion_loss")


def _first_number(d: dict[str, Any], keys: tuple[str, ...]) -> Optional[float]:
    """Return the first numeric value found under any of `keys`, walking
    both the top-level dict and its `key_specs` / `specs` sub-dict."""
    specs = d.get("key_specs") or d.get("specs") or {}
    for k in keys:
        for source in (d, specs):
            if not isinstance(source, dict):
                continue
            v = source.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                # Distributor strings like "+15 dB" / "-1.5 dB"
                import re as _re
                m = _re.search(r"-?\d+(?:\.\d+)?", str(v))
                if m:
                    try:
                        return float(m.group(0))
                    except ValueError:
                        continue
    return None


# Categories that contribute to the RF cascade. Passive filters / pads
# that the LLM emits as "insertion_loss_db" become negative gain stages.
_ACTIVE_CATEGORIES = {
    "RF-LNA", "LNA", "RF-Amplifier", "Amplifier", "RF-PA",
    "RF-Mixer", "Mixer", "RF-Downconverter",
    "RF-ADC", "ADC", "RF-VGA", "VGA",
    "RF-Filter", "Filter", "RF-Attenuator", "Attenuator",
    "RF-Coupler", "Coupler",
}


def _is_rf_stage(c: dict[str, Any]) -> bool:
    cat = str(c.get("category") or "").strip()
    if cat in _ACTIVE_CATEGORIES:
        return True
    # Signal-chain heuristic: if the component advertises NF / gain, it's
    # in the chain regardless of how its category was labelled.
    if _first_number(c, _NF_KEYS) is not None:
        return True
    if _first_number(c, _GAIN_KEYS) is not None:
        return True
    if _first_number(c, _LOSS_KEYS) is not None:
        return True
    return False


def extract_stages(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter `components` down to the RF-cascade-relevant ones and
    normalise each entry to `{name, part_number, category, nf_db, gain_db, iip3_dbm}`.

    When a component advertises `insertion_loss_db` instead of gain (i.e.
    passive filters), we treat it as -loss dB gain so it still shows up
    in the cumulative calculation.
    """
    stages: list[dict[str, Any]] = []
    for c in components or []:
        if not isinstance(c, dict):
            continue
        if not _is_rf_stage(c):
            continue
        nf = _first_number(c, _NF_KEYS)
        gain = _first_number(c, _GAIN_KEYS)
        if gain is None:
            loss = _first_number(c, _LOSS_KEYS)
            if loss is not None:
                gain = -abs(loss)
        iip3 = _first_number(c, _IIP3_KEYS)
        stages.append({
            "name": (c.get("component_name")
                     or c.get("function")
                     or c.get("part_number")
                     or c.get("name")
                     or "stage"),
            "part_number": c.get("part_number") or "",
            "category": c.get("category") or "",
            "nf_db": nf,
            "gain_db": gain,
            "iip3_dbm": iip3,
        })
    return stages


# ---------------------------------------------------------------------------
# Friis cascade
# ---------------------------------------------------------------------------

def _db_to_lin(x: float) -> float:
    return 10.0 ** (x / 10.0)


def _lin_to_db(x: float) -> float:
    if x <= 0:
        # Friis asymptote — a perfectly noiseless / infinite-IP3 stage
        # would land here. Clamp so the renderer doesn't get -inf.
        return float("-inf")
    return 10.0 * math.log10(x)


def compute_cascade(
    components: list[dict[str, Any]],
    *,
    claimed_nf_db: Optional[float] = None,
    claimed_iip3_dbm: Optional[float] = None,
    claimed_total_gain_db: Optional[float] = None,
) -> dict[str, Any]:
    """Walk the RF stages left-to-right and compute Friis cascade totals.

    Returns a dict with:
      - `stages`: [{name, part_number, category, nf_db, gain_db, iip3_dbm,
                    cum_gain_db, cum_nf_db, cum_iip3_dbm,
                    contribution_nf, contribution_iip3}]
      - `totals`: {nf_db, gain_db, iip3_dbm}
      - `claims`: {nf_db, iip3_dbm, total_gain_db} (echoed back for the UI)
      - `verdict`: {nf_pass, iip3_pass, gain_pass, nf_headroom_db, ...}
        — each flag is True when the measured cascade meets the claim,
          False when it violates, None when no claim was supplied.

    Stages with missing nf_db / gain_db are skipped in the cumulative
    calculation (their cum_* values carry forward from the previous
    stage). This mirrors what an RF engineer does manually when a
    passive coupling cap has no spec — you just assume 0 dB.
    """
    stages = extract_stages(components)

    cum_g_lin = 1.0    # cumulative gain at the *input* of stage i
    cum_nf_lin = 1.0   # cumulative noise factor at the output so far (=1 before any stage)
    cum_recip_iip3_lin = 0.0  # sum of (g_preceding / iip3_i_lin)

    total_gain_db = 0.0
    first_active = True

    for s in stages:
        nf = s["nf_db"]
        g = s["gain_db"]
        iip3 = s["iip3_dbm"]

        # Gain accumulates unconditionally (0 dB when missing).
        g_lin = _db_to_lin(g) if g is not None else 1.0
        if g is not None:
            total_gain_db += g

        # NF Friis — only advance when we actually have NF for this stage.
        nf_contribution: Optional[float] = None
        if nf is not None:
            nf_lin = _db_to_lin(nf)
            if first_active:
                cum_nf_lin = nf_lin
                nf_contribution = nf  # the very first stage sets the floor
                first_active = False
            else:
                contribution = (nf_lin - 1.0) / cum_g_lin
                cum_nf_lin += contribution
                nf_contribution = _lin_to_db(1.0 + contribution) if contribution > 0 else 0.0

        # IIP3 Friis — use the gain *preceding* this stage.
        iip3_contribution: Optional[float] = None
        if iip3 is not None:
            iip3_lin_watts = _db_to_lin(iip3) / 1000.0  # dBm → mW → W
            if iip3_lin_watts > 0:
                cum_recip_iip3_lin += cum_g_lin / iip3_lin_watts
                iip3_contribution = iip3

        # Advance the running input-gain for the next stage *after* we've
        # consumed this one with the pre-stage gain.
        cum_g_lin *= g_lin

        s["cum_gain_db"] = _lin_to_db(cum_g_lin)
        s["cum_nf_db"] = (_lin_to_db(cum_nf_lin)
                          if cum_nf_lin > 0 and not first_active else None)
        s["cum_iip3_dbm"] = (
            _lin_to_db(1.0 / cum_recip_iip3_lin * 1000.0)
            if cum_recip_iip3_lin > 0 else None
        )
        s["nf_contribution_db"] = nf_contribution
        s["iip3_contribution_dbm"] = iip3_contribution

    totals = {
        "nf_db": (_lin_to_db(cum_nf_lin) if not first_active else None),
        "gain_db": total_gain_db if stages else None,
        "iip3_dbm": (_lin_to_db(1.0 / cum_recip_iip3_lin * 1000.0)
                     if cum_recip_iip3_lin > 0 else None),
        "stage_count": len(stages),
    }

    verdict: dict[str, Any] = {
        "nf_pass": None, "gain_pass": None, "iip3_pass": None,
        "nf_headroom_db": None, "iip3_headroom_db": None, "gain_delta_db": None,
    }
    if claimed_nf_db is not None and totals["nf_db"] is not None:
        verdict["nf_headroom_db"] = float(claimed_nf_db) - totals["nf_db"]
        verdict["nf_pass"] = totals["nf_db"] <= float(claimed_nf_db)
    if claimed_iip3_dbm is not None and totals["iip3_dbm"] is not None:
        verdict["iip3_headroom_db"] = totals["iip3_dbm"] - float(claimed_iip3_dbm)
        verdict["iip3_pass"] = totals["iip3_dbm"] >= float(claimed_iip3_dbm)
    if claimed_total_gain_db is not None and totals["gain_db"] is not None:
        verdict["gain_delta_db"] = totals["gain_db"] - float(claimed_total_gain_db)
        # Allow ±3 dB slack on gain — it's usually VGA-adjustable.
        verdict["gain_pass"] = abs(verdict["gain_delta_db"]) <= 3.0

    return {
        "stages": stages,
        "totals": totals,
        "claims": {
            "nf_db": claimed_nf_db,
            "iip3_dbm": claimed_iip3_dbm,
            "total_gain_db": claimed_total_gain_db,
        },
        "verdict": verdict,
    }
