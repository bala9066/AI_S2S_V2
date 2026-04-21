"""
Post-LLM RF audit checks — P0.1 / P0.2 / P1.5 / P1.6 wiring.

When the P1 `generate_requirements` tool call returns, we now run three
**structural** checks on the output before it reaches the BOM:

  1. Block-diagram topology matches the wizard-selected architecture
     (tools/block_diagram_validator.py).
  2. Every `datasheet_url` actually resolves (tools/datasheet_verify.py),
     or at least points at a curated trusted-vendor domain when the
     deployment is air-gapped.
  3. No component is on the banned-manufacturer or EOL / NRND list
     (rules/banned_parts.py).

These checks produce `AuditIssue` rows that get merged into the
`AuditReport` the finalize step was already building, so the existing
UI rendering + overall_pass gating picks them up automatically.

Controlled by one env var for air-gapped demos:
  SKIP_DATASHEET_VERIFY=1  →  skip the network HEAD probes, fall back
                              to the trusted-vendor allowlist only.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from domains._schema import AuditIssue
from rules.banned_parts import filter_components
from tools.block_diagram_validator import validate as _validate_topology
from tools.datasheet_verify import is_trusted_vendor_url, verify_url
from tools.distributor_search import (
    any_api_configured as _distributor_configured,
    lookup as _distributor_lookup,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def run_topology_audit(
    mermaid: Optional[str],
    architecture: Optional[str],
) -> list[AuditIssue]:
    """Run the block-diagram topology validator and translate its
    violations into `AuditIssue` rows. An empty Mermaid input yields one
    critical issue (the diagram must exist)."""
    violations = _validate_topology(mermaid or "", architecture=architecture)
    issues: list[AuditIssue] = []
    for v in violations:
        issues.append(AuditIssue(
            severity=v.severity,  # validator uses the same four labels
            category="topology",
            location="block_diagram_mermaid",
            detail=v.detail,
            suggested_fix=v.suggested_fix,
        ))
    return issues


# ---------------------------------------------------------------------------
# Datasheet URLs
# ---------------------------------------------------------------------------

def _should_verify_network() -> bool:
    return os.getenv("SKIP_DATASHEET_VERIFY", "").strip() not in {"1", "true", "yes"}


def run_datasheet_audit(
    component_recommendations: list[dict[str, Any]],
    *,
    timeout_s: float = 4.0,
    parallelism: int = 6,
) -> list[AuditIssue]:
    """Probe every `datasheet_url` — one issue per component whose URL
    neither resolves via HEAD/GET nor matches the trusted-vendor
    allowlist."""
    issues: list[AuditIssue] = []
    if not component_recommendations:
        return issues

    allow_network = _should_verify_network()

    # Build (index, url, component) triples so we can correlate
    # results with the original component row.
    targets: list[tuple[int, str, dict[str, Any]]] = []
    for idx, c in enumerate(component_recommendations):
        url = (c.get("datasheet_url") or c.get("datasheet") or "").strip()
        if not url:
            issues.append(_missing_url_issue(idx, c))
            continue
        targets.append((idx, url, c))

    if not targets:
        return issues

    # Short-circuit on the trusted-vendor allowlist first so air-gapped
    # environments (SKIP_DATASHEET_VERIFY=1) still mark known-good URLs
    # as verified without hitting the network.
    trusted: dict[int, bool] = {
        idx: is_trusted_vendor_url(url) for idx, url, _ in targets
    }

    live_results: dict[int, bool] = {}
    if allow_network:
        to_probe = [(idx, url) for idx, url, _ in targets if not trusted[idx]]
        if to_probe:
            with ThreadPoolExecutor(max_workers=parallelism) as ex:
                futures = {
                    ex.submit(verify_url, url, timeout=timeout_s): idx
                    for idx, url in to_probe
                }
                for fut in as_completed(futures):
                    idx = futures[fut]
                    try:
                        live_results[idx] = bool(fut.result())
                    except Exception as exc:  # noqa: BLE001
                        log.warning("datasheet_verify.exception idx=%s: %s", idx, exc)
                        live_results[idx] = False

    for idx, url, c in targets:
        if trusted[idx]:
            continue  # trusted-vendor URL → pass
        if live_results.get(idx, False):
            continue  # HEAD/GET resolved OK
        pn = c.get("part_number") or c.get("primary_part") or "unknown"
        suffix = "(network disabled)" if not allow_network else "(HEAD/GET failed)"
        issues.append(AuditIssue(
            severity="high",
            category="datasheet_url",
            location=f"component_recommendations/{pn}",
            detail=(
                f"Datasheet URL for `{pn}` did not resolve and is not on the "
                f"trusted-vendor allowlist: {url} {suffix}"
            ),
            suggested_fix=(
                "Replace with the manufacturer's canonical product-page URL "
                "(analog.com / ti.com / qorvo.com / macom.com etc.)."
            ),
        ))

    return issues


def _missing_url_issue(idx: int, c: dict[str, Any]) -> AuditIssue:
    pn = c.get("part_number") or c.get("primary_part") or f"row-{idx}"
    return AuditIssue(
        severity="medium",
        category="datasheet_url",
        location=f"component_recommendations/{pn}",
        detail=f"Component `{pn}` has no `datasheet_url` field.",
        suggested_fix="Populate `datasheet_url` with the manufacturer's product page.",
    )


# ---------------------------------------------------------------------------
# Banned parts
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Live part-number validation (DigiKey → Mouser → local seed)
# ---------------------------------------------------------------------------

def run_part_validation_audit(
    component_recommendations: list[dict[str, Any]],
    *, timeout_s: float = 6.0,
) -> tuple[list[dict[str, Any]], list[AuditIssue]]:
    """Look every MPN up via the distributor cascade. When a part is
    found we enrich the original component dict with the distributor's
    canonical manufacturer name, datasheet URL, and lifecycle status so
    downstream docs use the authoritative values, not the LLM's guesses.

    Issues produced:
      - `hallucinated_part` (critical) — MPN not found anywhere
      - `nrnd_part` (high) — found but flagged NRND by the distributor
      - `obsolete_part` (critical) — found but obsolete / discontinued

    Returns (enriched_components, issues).
    """
    issues: list[AuditIssue] = []
    enriched: list[dict[str, Any]] = []
    if not component_recommendations:
        return enriched, issues

    # When nobody is configured to look up live AND the seed file is
    # also unreachable, we can't distinguish hallucination from "no
    # oracle" — return without adding issues so the pipeline doesn't
    # hard-fail on every BOM in air-gap / CI.
    live_configured = _distributor_configured()

    for c in component_recommendations:
        pn = (
            c.get("part_number")
            or c.get("primary_part")
            or c.get("mpn")
            or ""
        ).strip()
        if not pn:
            enriched.append(c)
            continue

        info = None
        try:
            info = _distributor_lookup(pn, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001
            log.warning("distributor_lookup_failed pn=%s: %s", pn, exc)

        if info is None:
            # MPN unknown to every oracle we tried.
            if live_configured:
                issues.append(AuditIssue(
                    severity="critical",
                    category="hallucinated_part",
                    location=f"component_recommendations/{pn}",
                    detail=(
                        f"Part `{pn}` was not found on DigiKey, Mouser, or in "
                        "the local component seed — the LLM may have invented it."
                    ),
                    suggested_fix=(
                        "Replace with a verifiable active-production part from "
                        "data/sample_components.json or a real distributor MPN."
                    ),
                ))
            enriched.append(c)
            continue

        # Found — flag lifecycle issues before accepting.
        if info.lifecycle_status == "obsolete":
            issues.append(AuditIssue(
                severity="critical",
                category="obsolete_part",
                location=f"component_recommendations/{pn}",
                detail=(
                    f"Part `{pn}` is marked OBSOLETE by {info.source}. "
                    "Shipping an obsolete MPN risks immediate BOM redesign."
                ),
                suggested_fix="Replace with an active-production successor.",
            ))
        elif info.lifecycle_status == "nrnd":
            issues.append(AuditIssue(
                severity="high",
                category="nrnd_part",
                location=f"component_recommendations/{pn}",
                detail=(
                    f"Part `{pn}` is NRND (Not Recommended for New Designs) "
                    f"per {info.source}."
                ),
                suggested_fix="Prefer an active-production alternative for new builds.",
            ))

        # Enrich the component dict with authoritative values. The LLM's
        # fields survive only when the distributor didn't provide one.
        merged = {**c}
        if info.manufacturer:
            merged["manufacturer"] = info.manufacturer
        if info.datasheet_url:
            merged["datasheet_url"] = info.datasheet_url
        if info.lifecycle_status != "unknown":
            merged["lifecycle_status"] = info.lifecycle_status
        merged.setdefault("distributor_source", info.source)
        if info.product_url:
            merged.setdefault("product_url", info.product_url)
        if info.unit_price_usd is not None:
            merged.setdefault("unit_price_usd", info.unit_price_usd)
        enriched.append(merged)

    return enriched, issues


def run_banned_parts_audit(
    component_recommendations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[AuditIssue]]:
    """Filter banned / EOL / NRND parts out of the BOM.

    Returns (cleaned_bom, issues). Callers should replace the original
    `component_recommendations` array with `cleaned_bom` so downstream
    document generation doesn't emit the banned parts.
    """
    kept, rejected = filter_components(component_recommendations or [])
    issues = [AuditIssue(**rej.to_issue_dict()) for rej in rejected]
    return kept, issues


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_candidate_pool_audit(
    component_recommendations: list[dict[str, Any]],
    offered_mpns: Optional[set[str]],
) -> list[AuditIssue]:
    """Flag BOM entries whose MPN was NOT surfaced by find_candidate_parts.

    Retrieval-augmented selection requires the LLM to pick from the
    distributor shortlist.  When `offered_mpns` is empty / None we skip
    the check silently — not every conversation uses the retrieval tool
    yet (e.g. legacy runs, air-gap mode).  When the set is non-empty we
    flag every component whose MPN is not in it at severity="high":
    the part may still be real (rf_audit's hallucination check covers
    that), but it bypassed the process gate and deserves reviewer
    attention.
    """
    if not offered_mpns or not component_recommendations:
        return []
    offered_upper = {m.strip().upper() for m in offered_mpns if m}
    issues: list[AuditIssue] = []
    for c in component_recommendations:
        pn = (
            c.get("part_number")
            or c.get("primary_part")
            or c.get("mpn")
            or ""
        ).strip()
        if not pn:
            continue
        if pn.upper() in offered_upper:
            continue
        issues.append(AuditIssue(
            severity="high",
            category="not_from_candidate_pool",
            location=f"component_recommendations/{pn}",
            detail=(
                f"Part `{pn}` was not in the `find_candidate_parts` shortlist "
                "for this turn — the LLM either skipped the retrieval step or "
                "picked an MPN outside the returned candidates."
            ),
            suggested_fix=(
                "Re-run P1 and ensure the LLM calls find_candidate_parts for "
                "every signal-chain stage, then selects only from the returned "
                "`candidates[].part_number` list."
            ),
        ))
    return issues


def run_all(
    tool_input: dict[str, Any],
    architecture: Optional[str],
    *,
    timeout_s: float = 4.0,
    offered_candidate_mpns: Optional[set[str]] = None,
) -> tuple[dict[str, Any], list[AuditIssue]]:
    """Run every post-LLM check and return (possibly-mutated tool_input,
    combined issues). The tool_input is returned with banned parts
    removed from `component_recommendations` so the BOM the user sees is
    pre-filtered.

    When `offered_candidate_mpns` is supplied (the set of MPNs returned
    by `find_candidate_parts` during the same conversation turn), an
    extra `not_from_candidate_pool` audit issue is emitted for any BOM
    entry that bypassed the shortlist.
    """
    issues: list[AuditIssue] = []

    # 1. Topology
    issues.extend(run_topology_audit(
        tool_input.get("block_diagram_mermaid"),
        architecture,
    ))

    # 2. Banned parts — clean the BOM before the distributor lookup so we
    # don't waste API calls on parts we're about to drop anyway.
    bom_key = "component_recommendations"
    if bom_key not in tool_input and "bom" in tool_input:
        bom_key = "bom"
    original = tool_input.get(bom_key) or []
    cleaned, banned_issues = run_banned_parts_audit(original)
    issues.extend(banned_issues)

    # 3. Live part validation — DigiKey → Mouser → seed. Closes the
    # last-mile hallucination gap: parts the LLM invents and that aren't
    # in any distributor catalogue get flagged here. Also enriches
    # component dicts with the distributor's canonical manufacturer +
    # datasheet URL, so downstream docs use authoritative values.
    enriched, part_issues = run_part_validation_audit(cleaned, timeout_s=timeout_s)
    issues.extend(part_issues)

    # Persist the cleaned + enriched BOM back onto tool_input
    if banned_issues or part_issues or enriched != cleaned:
        tool_input = {**tool_input, bom_key: enriched}

    # 4. Datasheet URLs (on the enriched BOM — post-distributor, the URLs
    # should mostly be authoritative already, but any that still slipped
    # through get validated here as a belt-and-braces check).
    issues.extend(run_datasheet_audit(enriched, timeout_s=timeout_s))

    # 5. Candidate-pool gate — advisory check that the LLM used the
    # retrieval tool. Only fires when the caller threaded the set in.
    issues.extend(run_candidate_pool_audit(enriched, offered_candidate_mpns))

    return tool_input, issues
