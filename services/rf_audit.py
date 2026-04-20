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

def run_all(
    tool_input: dict[str, Any],
    architecture: Optional[str],
    *,
    timeout_s: float = 4.0,
) -> tuple[dict[str, Any], list[AuditIssue]]:
    """Run every post-LLM check and return (possibly-mutated tool_input,
    combined issues). The tool_input is returned with banned parts
    removed from `component_recommendations` so the BOM the user sees is
    pre-filtered."""
    issues: list[AuditIssue] = []

    # 1. Topology
    issues.extend(run_topology_audit(
        tool_input.get("block_diagram_mermaid"),
        architecture,
    ))

    # 2. Banned parts — clean the BOM before the datasheet probe so we
    # don't waste HEAD requests on parts we're about to drop anyway.
    bom_key = "component_recommendations"
    if bom_key not in tool_input and "bom" in tool_input:
        bom_key = "bom"
    original = tool_input.get(bom_key) or []
    cleaned, banned_issues = run_banned_parts_audit(original)
    issues.extend(banned_issues)
    if banned_issues:
        tool_input = {**tool_input, bom_key: cleaned}

    # 3. Datasheet URLs (on the cleaned BOM)
    issues.extend(run_datasheet_audit(cleaned, timeout_s=timeout_s))

    return tool_input, issues
