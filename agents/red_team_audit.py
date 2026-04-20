"""
Red-Team Audit Agent — B2.1 (Workstream B / AI-ML).

This agent is the pipeline's internal adversary. It runs AFTER the P1 requirements
agent has produced a BOM, block diagram, and cascade claims, but BEFORE the
requirements are locked (and therefore before any downstream phase can consume them).

The red-team's job: try to break the output. Specifically:
  1. Re-compute the RF cascade from the BOM and compare to the agent's claims.
  2. Verify every cited standard/clause against the clause DB.
  3. Check that every claimed part number exists in the component DB OR has a
     resolvable datasheet URL.
  4. Flag unresolved citations, hallucinated claims, and cascade contradictions.

Output is a structured AuditReport (domains._schema.AuditReport). The P1 agent
is required to consume this report and either (a) fix the flagged issues, or
(b) return them to the user for clarification. The requirements cannot be
LOCKED until the audit report has `overall_pass=True`.

Status: SKELETON — this is the 0.1 scaffold. The TODO markers are the Week 2
follow-up tickets listed in IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from domains._schema import AuditIssue, AuditReport
from domains.standards import validate_citations
from tools.cascade_validator import validate_cascade_from_dicts


# ---------------------------------------------------------------------------
# Claim extraction — structured input is preferred; the fallback parses prose.
# ---------------------------------------------------------------------------

_DB_CLAIM_RE = re.compile(
    r"(?P<metric>noise\s*figure|NF|IIP3|P1dB|sensitivity|gain|SFDR)"
    r"[^\d\-]*"
    r"(?P<sign>-?)\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>dBm|dB)",
    re.IGNORECASE,
)


def extract_numeric_claims_from_text(prose: str) -> list[dict[str, Any]]:
    """
    Fallback extractor when the requirements_agent gives free-form prose.
    Prefer structured `claimed_cascade` dict input — this is best-effort.
    """
    claims: list[dict[str, Any]] = []
    for m in _DB_CLAIM_RE.finditer(prose):
        metric = m.group("metric").lower().replace(" ", "")
        metric = {"noisefigure": "noise_figure_db", "nf": "noise_figure_db"}.get(metric, metric)
        value = float(m.group("sign") + m.group("value"))
        unit = m.group("unit").lower()
        claims.append({"metric": metric, "value": value, "unit": unit})
    return claims


# ---------------------------------------------------------------------------
# Individual checks — each returns zero or more AuditIssue objects.
# ---------------------------------------------------------------------------

def _issue(severity: str, category: str, location: str, detail: str,
           suggested_fix: Optional[str] = None) -> AuditIssue:
    return AuditIssue(
        severity=severity,       # critical | high | medium | low | info
        category=category,
        location=location,
        detail=detail,
        suggested_fix=suggested_fix,
    )


def check_cascade_vs_claims(
    bom_stages: list[dict[str, Any]],
    claimed: dict[str, Any],
    bandwidth_hz: float,
    snr_required_db: float,
    temperature_c: float,
    tolerance_db: float = 1.0,
) -> tuple[dict[str, Any], list[AuditIssue]]:
    """
    Recompute the cascade and compare against the agent's claimed values.

    `claimed` may contain (optional) keys: noise_figure_db, total_gain_db,
    iip3_dbm_input, sensitivity_dbm, sfdr_db.
    """
    issues: list[AuditIssue] = []
    rep = validate_cascade_from_dicts(
        stages=bom_stages,
        bandwidth_hz=bandwidth_hz,
        snr_required_db=snr_required_db,
        temperature_c=temperature_c,
    )

    for key, attr in [
        ("noise_figure_db", "noise_figure_db"),
        ("total_gain_db", "total_gain_db"),
        ("iip3_dbm_input", "iip3_dbm_input"),
        ("sensitivity_dbm", "sensitivity_dbm"),
        ("sfdr_db", "sfdr_db"),
    ]:
        if key not in claimed or claimed[key] is None:
            continue
        computed = getattr(rep, attr)
        if computed is None:
            continue
        delta = abs(float(claimed[key]) - float(computed))
        if delta > tolerance_db:
            issues.append(_issue(
                severity="high",
                category="cascade_mismatch",
                location=f"cascade.{key}",
                detail=(
                    f"Agent claimed {key} = {claimed[key]:.2f} but cascade "
                    f"validator computes {computed:.2f} "
                    f"(delta {delta:.2f} dB > {tolerance_db} dB tolerance)."
                ),
                suggested_fix=(
                    f"Update the claimed {key} to {computed:.2f} OR revisit the "
                    "BOM (stage gains / NFs / IIP3s) to meet the claim."
                ),
            ))

    # Surface validator's own warnings/errors as audit issues.
    for w in rep.warnings:
        issues.append(_issue(
            severity="medium",
            category="cascade_rule",
            location="cascade",
            detail=w,
        ))
    for e in rep.errors:
        issues.append(_issue(
            severity="critical",
            category="cascade_rule",
            location="cascade",
            detail=e,
        ))
    return rep.to_dict(), issues


def check_citations(citations: list[tuple[str, str]]) -> list[AuditIssue]:
    """Validate every (standard, clause) pair resolves in the standards DB."""
    issues: list[AuditIssue] = []
    missing = validate_citations(citations)
    for std, cl in missing:
        issues.append(_issue(
            severity="high",
            category="unresolved_citation",
            location=f"citations[{std} {cl}]",
            detail=f"Citation '{std} {cl}' does not resolve in the clause DB.",
            suggested_fix="Replace with a verified clause or remove the citation.",
        ))
    return issues


def check_cosite_imd(
    freq_range_mhz: tuple[float, float],
    cosite_emitters_mhz: list[float],
    receiver_iip3_dbm: Optional[float] = None,
    antenna_isolation_db: Optional[float] = None,
    emitter_power_dbm: float = 30.0,
    product_threshold_dbm: float = -90.0,
) -> list[AuditIssue]:
    """
    Compute third-order intermodulation products (2*f1 - f2 and 2*f2 - f1) from
    co-site emitter pairs and flag any product that falls inside the receiver's
    operating band [freq_range_mhz[0], freq_range_mhz[1]].

    If `receiver_iip3_dbm` and `antenna_isolation_db` are both provided, also
    estimates the IMD3 product power referenced to the LNA input using the
    classical relation:
        P_tone_in  = emitter_power_dbm - antenna_isolation_db
        P_IMD3_in  = 3 * P_tone_in - 2 * receiver_iip3_dbm
    Products whose estimated power is above `product_threshold_dbm` are marked
    critical; in-band products without a power estimate are marked high.

    Parameters
    ----------
    freq_range_mhz:
        (low, high) receiver operating band in MHz, inclusive.
    cosite_emitters_mhz:
        Carrier frequencies of co-sited transmitters in MHz.
    receiver_iip3_dbm, antenna_isolation_db:
        Optional. When both are given, an IMD3 power estimate is computed.
    emitter_power_dbm:
        Assumed transmit power of each co-sited emitter (default +30 dBm,
        representative of a 1 W handheld).
    product_threshold_dbm:
        Above this input-referred IMD3 level, the product is treated as
        blocker-class (critical).
    """
    issues: list[AuditIssue] = []
    if not cosite_emitters_mhz or len(cosite_emitters_mhz) < 2:
        return issues
    f_lo, f_hi = float(freq_range_mhz[0]), float(freq_range_mhz[1])
    if f_lo > f_hi:
        f_lo, f_hi = f_hi, f_lo

    seen: set[tuple[float, float, float]] = set()  # dedupe symmetric pairs

    imd3_power_dbm: Optional[float] = None
    if receiver_iip3_dbm is not None and antenna_isolation_db is not None:
        p_tone_in = emitter_power_dbm - float(antenna_isolation_db)
        imd3_power_dbm = 3.0 * p_tone_in - 2.0 * float(receiver_iip3_dbm)

    for f1 in cosite_emitters_mhz:
        for f2 in cosite_emitters_mhz:
            if f1 == f2:
                continue
            for product in (2.0 * f1 - f2, 2.0 * f2 - f1):
                if not (f_lo <= product <= f_hi):
                    continue
                key = (round(min(f1, f2), 3), round(max(f1, f2), 3), round(product, 3))
                if key in seen:
                    continue
                seen.add(key)
                detail = (
                    f"Third-order intermod product 2*{f1} - {f2} = {product:.3f} MHz "
                    f"(or conjugate) falls inside receiver band "
                    f"[{f_lo:.3f}, {f_hi:.3f}] MHz."
                )
                if imd3_power_dbm is not None:
                    detail += (
                        f" Estimated IMD3 at LNA input = {imd3_power_dbm:.1f} dBm "
                        f"(emitter {emitter_power_dbm:.1f} dBm, isolation "
                        f"{antenna_isolation_db:.1f} dB, receiver IIP3 "
                        f"{receiver_iip3_dbm:.1f} dBm)."
                    )
                    severity = "critical" if imd3_power_dbm > product_threshold_dbm else "medium"
                else:
                    severity = "high"
                issues.append(_issue(
                    severity=severity,
                    category="cosite_imd",
                    location=f"cosite_imd[{f1}MHz,{f2}MHz]",
                    detail=detail,
                    suggested_fix=(
                        "Add a pre-select filter to suppress the offending emitter(s), "
                        "increase antenna isolation, or raise the receiver IIP3."
                    ),
                ))
    return issues


def check_part_numbers(
    claimed_parts: list[dict[str, Any]],
    known_parts: set[str],
) -> list[AuditIssue]:
    """
    Flag any part number in the BOM that is not present in the domain DB AND
    does not have a datasheet URL on file. This catches fabricated part numbers.
    """
    issues: list[AuditIssue] = []
    for idx, p in enumerate(claimed_parts):
        pn = str(p.get("part_number", "")).strip()
        if not pn:
            issues.append(_issue(
                severity="medium",
                category="missing_part_number",
                location=f"bom[{idx}]",
                detail=f"Component entry has no part number: {p.get('description', p)}",
            ))
            continue
        if pn in known_parts:
            continue
        if not p.get("datasheet_url"):
            issues.append(_issue(
                severity="critical",
                category="hallucinated_part",
                location=f"bom[{idx}].part_number={pn}",
                detail=(
                    f"Part number '{pn}' is not in the component DB and has no "
                    "datasheet URL — likely fabricated."
                ),
                suggested_fix="Replace with a real part or add a verified datasheet URL.",
            ))
    return issues


# v16 — exact-match blacklist for parts the LLM has been observed to recommend
# despite being NRND / EOL / discontinued. Update when the demo surfaces a new
# stale pick. Kept uppercase for case-insensitive compare.
_STALE_MPN_BLACKLIST: frozenset[str] = frozenset({
    # Analog Devices — legacy Hittite MMIC catalogue, mostly NRND by 2024
    "HMC-C024", "HMC-C070", "HMC-C072", "HMC-ALH435", "HMC-ALH508",
    "HMC516", "HMC516LC5",
    "HMC1040", "HMC1040LP5CE", "HMC1040LP4E",
    "HMC1020", "HMC1020LP4E",
    # Rohm — chip resistor variant DigiKey flagged discontinued
    "MCR03ERTJ201",
    # VPT Inc. — banned manufacturer already; catching any that slip through
    "VPT100", "VPT200",
})

# v16 — regex families for broad stale patterns (Analog Devices' legacy
# Hittite catalogue naming convention). A part matching these is almost
# always NRND and needs review.
_STALE_MPN_PATTERNS: list[str] = [
    # HMC-Cnnn (connectorised Hittite modules, ~2008-2012 catalogue)
    r"^HMC-?C\d{3,4}$",
    # HMC-ALHnnn (legacy Hittite amplifier line)
    r"^HMC-?ALH\d{3,4}$",
]


def _is_stale_mpn(pn: str) -> tuple[bool, str]:
    """Return (is_stale, reason). Checks exact blacklist + known legacy patterns."""
    if not pn:
        return (False, "")
    key = pn.strip().upper()
    if key in _STALE_MPN_BLACKLIST:
        return (True, "exact-blacklist")
    for pat in _STALE_MPN_PATTERNS:
        if re.match(pat, key):
            return (True, f"pattern:{pat}")
    return (False, "")


def check_lifecycle(claimed_parts: list[dict[str, Any]]) -> list[AuditIssue]:
    """
    v16 — flag any BOM entry whose part number is on the stale blacklist OR
    matches a known-stale pattern OR has a non-"active" `lifecycle_status`
    declaration. This runs regardless of the LLM's own claim, so even if the
    model hallucinates `lifecycle_status: active` on a blacklisted part, the
    red-team catches it.
    """
    issues: list[AuditIssue] = []
    for idx, p in enumerate(claimed_parts):
        pn = str(p.get("part_number", "")).strip()
        stale, reason = _is_stale_mpn(pn)
        if stale:
            issues.append(_issue(
                severity="critical",
                category="stale_part",
                location=f"bom[{idx}].part_number={pn}",
                detail=(
                    f"Part '{pn}' is on the stale-parts blacklist ({reason}) — "
                    "this part is NRND / EOL / discontinued by the manufacturer "
                    "and must not be used in new designs."
                ),
                suggested_fix=(
                    "Pick an active successor from the same manufacturer family. "
                    "Broadband RF LNA alternatives: HMC8410, HMC8411, ADL8104, "
                    "ADL8106 (Analog Devices), QPL9057, TQL9066, TQL9092 (Qorvo), "
                    "PMA3-83LN+, PSA4-5043+ (Mini-Circuits). Broadband limiter "
                    "alternatives: MADL-011017, MADL-011019 (MACOM), SKY16406-321LF "
                    "(Skyworks), TGL2222 (Qorvo)."
                ),
            ))
            continue
        # Fall-through check: the LLM explicitly marked the part non-active.
        status = str(p.get("lifecycle_status", "") or "").strip().lower()
        if status and status != "active":
            issues.append(_issue(
                severity="high",
                category="non_active_lifecycle",
                location=f"bom[{idx}].part_number={pn}",
                detail=(
                    f"Component '{pn}' declares lifecycle_status='{status}' — "
                    "only 'active' (currently in production) parts are permitted."
                ),
                suggested_fix="Replace with an actively-produced alternative.",
            ))
    return issues


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def audit(
    phase_id: str,
    bom_stages: list[dict[str, Any]],
    claimed_cascade: dict[str, Any],
    citations: list[tuple[str, str]],
    claimed_parts: list[dict[str, Any]],
    known_parts: Optional[set[str]] = None,
    bandwidth_hz: float = 1_000_000.0,
    snr_required_db: float = 10.0,
    temperature_c: float = 25.0,
    cascade_tolerance_db: float = 1.0,
    cosite_context: Optional[dict[str, Any]] = None,
) -> AuditReport:
    """
    Run all red-team checks and return a combined AuditReport.

    Parameters
    ----------
    phase_id:
        Which phase this audit was run against (e.g. "P1").
    bom_stages:
        Ordered list of stage dicts suitable for `validate_cascade_from_dicts`.
    claimed_cascade:
        What the P1 agent CLAIMED the cascade numbers are. Keys may include
        any of: noise_figure_db, total_gain_db, iip3_dbm_input, sensitivity_dbm, sfdr_db.
    citations:
        List of (standard, clause) pairs cited anywhere in the generated output.
    claimed_parts:
        List of part dicts from the BOM. Each must include `part_number`.
    known_parts:
        Optional set of all part numbers present in the component DB for the
        active domain. Parts not in this set and without a datasheet URL are
        flagged as hallucinated.
    bandwidth_hz, snr_required_db, temperature_c:
        Context for the cascade recalc.
    """
    issues: list[AuditIssue] = []

    _rep_dict, cascade_issues = check_cascade_vs_claims(
        bom_stages=bom_stages,
        claimed=claimed_cascade,
        bandwidth_hz=bandwidth_hz,
        snr_required_db=snr_required_db,
        temperature_c=temperature_c,
        tolerance_db=cascade_tolerance_db,
    )
    issues.extend(cascade_issues)

    issues.extend(check_citations(citations))
    issues.extend(check_part_numbers(claimed_parts, known_parts or set()))
    # v16 — lifecycle gate: blocks EOL/NRND/discontinued parts even when the
    # LLM claims lifecycle_status="active" (hardcoded blacklist wins).
    issues.extend(check_lifecycle(claimed_parts))

    if cosite_context:
        issues.extend(check_cosite_imd(
            freq_range_mhz=tuple(cosite_context["freq_range_mhz"]),  # type: ignore[arg-type]
            cosite_emitters_mhz=list(cosite_context.get("cosite_emitters_mhz", [])),
            receiver_iip3_dbm=cosite_context.get("receiver_iip3_dbm"),
            antenna_isolation_db=cosite_context.get("antenna_isolation_db"),
            emitter_power_dbm=cosite_context.get("emitter_power_dbm", 30.0),
            product_threshold_dbm=cosite_context.get("product_threshold_dbm", -90.0),
        ))

    cascade_errors = sum(1 for i in issues if i.category in ("cascade_mismatch", "cascade_rule")
                         and i.severity in ("critical", "high"))
    # v16 — treat stale-parts and non-active lifecycle as hallucinations for the
    # summary counter so the chat reply surfaces "N blockers" accurately.
    hallucination_count = sum(
        1 for i in issues
        if i.category in ("hallucinated_part", "stale_part", "non_active_lifecycle")
    )
    unresolved_citations = sum(1 for i in issues if i.category == "unresolved_citation")

    # Simple confidence heuristic.
    weight = {"critical": 0.20, "high": 0.12, "medium": 0.05, "low": 0.02, "info": 0.0}
    confidence_score = max(0.0, 1.0 - sum(weight.get(i.severity, 0.05) for i in issues))

    overall_pass = not any(i.severity in ("critical", "high") for i in issues)

    return AuditReport(
        phase_id=phase_id,
        issues=issues,
        hallucination_count=hallucination_count,
        unresolved_citations=unresolved_citations,
        cascade_errors=cascade_errors,
        overall_pass=overall_pass,
        confidence_score=round(confidence_score, 3),
    )


# ---------------------------------------------------------------------------
# TODO — Week 2 follow-ups (tracked in IMPLEMENTATION_PLAN.md B2.2+)
# ---------------------------------------------------------------------------
# - Actually resolve datasheet URLs (HEAD request) in check_part_numbers; record
#   result in the `parts.datasheet_verified` column.
# - Add a blocker-signal check: if the architecture has a mixer and the user
#   declared a co-site jammer, compute third-order products and flag overlaps
#   with the signal band.
# - Add a "model-on-model" critic pass: run the final output through a different
#   LLM (fallback tier) and require it to agree. Treat disagreements as warnings.
# - Surface the AuditReport into the frontend as a side panel on P1; the user
#   sees EXACTLY what the red-team found.
