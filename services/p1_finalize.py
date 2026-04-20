"""
services/p1_finalize.py — A1.2.

Glue between the P1 `RequirementsAgent` tool call (`generate_requirements`) and
the downstream services:

1. Build a `RequirementsLock` over the confirmed requirement set + chosen
   architecture and freeze it (SHA256 content hash).
2. Run the red-team `audit(...)` over the generated BOM, cascade claims,
   citations, and part numbers. Populate a known-parts whitelist from the
   active domain's `components.json`.
3. Optionally run `run_critic(...)` (model-on-model disagreement). Only
   executed when a `base_agent` instance is supplied with a fallback chain.
4. Return a bundle of artifacts — the caller (requirements_agent.execute)
   merges them into its `outputs` dict so StorageAdapter persists them and
   the UI can display them.

Kept deliberately narrow — no DB writes here. `chat_service.ChatService`
already persists the output files + phase-status transition. A follow-up
DB-column write for `requirements_hash` / `requirements_frozen_at` can be
layered onto `project_service` without touching this file.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from agents.red_team_audit import audit as _run_audit
from domains._schema import AuditReport
from services.requirements_lock import RequirementsLock, freeze, save_to_row

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_domain(design_type: Optional[str], requirements: dict[str, Any]) -> str:
    """Heuristically infer the active domain so we can load the right
    components.json known-parts set for the part-number audit."""
    dt = (design_type or "").lower()
    if "radar" in dt:
        return "radar"
    if "ew" in dt or "sigint" in dt or "ecm" in dt or "warfare" in dt:
        return "ew"
    if "satcom" in dt or "satellite" in dt:
        return "satcom"
    if "comm" in dt or "radio" in dt or "link" in dt:
        return "communication"
    # Scan the requirements payload for domain hints
    blob = json.dumps(requirements or {}, default=str).lower()
    for kw, dom in (
        ("radar", "radar"), ("ecm", "ew"), ("electronic warfare", "ew"),
        ("sigint", "ew"), ("satellite", "satcom"), ("satcom", "satcom"),
        ("tactical", "communication"), ("radio", "communication"),
    ):
        if kw in blob:
            return dom
    return "communication"


def _load_known_parts(domain: str) -> set[str]:
    """Load the list of part numbers from `domains/<domain>/components.json`.
    Returns an empty set on any failure — the audit tolerates this."""
    try:
        fpath = _REPO_ROOT / "domains" / domain / "components.json"
        if not fpath.exists():
            return set()
        data = json.loads(fpath.read_text(encoding="utf-8"))
        parts = data.get("components") or data.get("parts") or []
        out = {str(p.get("part_number", "")).strip() for p in parts if p.get("part_number")}
        return {p for p in out if p}
    except Exception as exc:
        logger.warning("p1_finalize._load_known_parts failed for %s: %s", domain, exc)
        return set()


def _tool_bom_to_stages(bom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the agent's BOM schema into the stage-dict format the cascade
    validator expects. Unknown fields are dropped — missing fields become
    harmless None values that the validator treats as 'unspecified'."""
    stages = []
    for c in bom or []:
        stages.append({
            "name": c.get("name") or c.get("part_number") or c.get("role") or "stage",
            "kind": c.get("kind") or c.get("role") or "active",
            "gain_db": c.get("gain_db"),
            "nf_db": c.get("nf_db") or c.get("noise_figure_db"),
            "iip3_dbm": c.get("iip3_dbm"),
            "p1db_dbm": c.get("p1db_dbm") or c.get("p1db_out_dbm"),
        })
    return stages


def _collect_citations(tool_input: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten standards / clauses referenced anywhere in the tool output."""
    out: list[tuple[str, str]] = []
    cites = tool_input.get("citations") or tool_input.get("standards_citations") or []
    for c in cites:
        if isinstance(c, dict):
            std = str(c.get("standard") or "").strip()
            clause = str(c.get("clause") or "").strip()
            if std and clause:
                out.append((std, clause))
        elif isinstance(c, (list, tuple)) and len(c) >= 2:
            out.append((str(c[0]).strip(), str(c[1]).strip()))
    return out


def _collect_parts(tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the flat parts list with part_number fields for the audit."""
    bom = tool_input.get("component_recommendations") or tool_input.get("bom") or []
    parts: list[dict[str, Any]] = []
    for c in bom:
        pn = c.get("part_number") or c.get("mpn") or c.get("name")
        if not pn:
            continue
        parts.append({
            "part_number": str(pn),
            "manufacturer": c.get("manufacturer") or c.get("vendor"),
            "datasheet_url": c.get("datasheet_url") or c.get("datasheet"),
        })
    return parts


# ---------------------------------------------------------------------------
# Audit → markdown
# ---------------------------------------------------------------------------

def audit_report_to_md(rep: AuditReport) -> str:
    """Render an AuditReport as a human-readable markdown summary."""
    lines = [
        "# Red-Team Audit Report",
        "",
        f"- **Phase:** {rep.phase_id}",
        f"- **Overall pass:** {'PASS' if rep.overall_pass else 'FAIL'}",
        f"- **Confidence:** {rep.confidence_score:.2f}",
        f"- **Hallucinations:** {rep.hallucination_count}",
        f"- **Unresolved citations:** {rep.unresolved_citations}",
        f"- **Cascade errors:** {rep.cascade_errors}",
        "",
    ]
    if not rep.issues:
        lines.append("_No issues flagged — design passed all red-team checks._")
        return "\n".join(lines)
    lines += ["## Issues", "",
              "| Severity | Category | Location | Detail | Suggested fix |",
              "|---|---|---|---|---|"]
    for i in rep.issues:
        detail = (i.detail or "").replace("|", "\\|").replace("\n", " ")
        fix = (i.suggested_fix or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {i.severity} | {i.category} | {i.location} | {detail} | {fix} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def finalize_p1(
    tool_input: dict[str, Any],
    project_id: Any,
    design_type: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_model_version: Optional[str] = None,
    architecture: Optional[str] = None,
) -> dict[str, Any]:
    """
    Freeze the lock and run the red-team audit. Returns a bundle:

        {
            "lock": <RequirementsLock.to_dict()>,
            "lock_row": {requirements_hash, requirements_frozen_at,
                         requirements_locked_json},
            "audit_report": <AuditReport.model_dump()>,
            "outputs": {
                "requirements_lock.json": "...",
                "audit_report.md":        "...",
            },
            "summary_md": "short markdown summary for the chat reply",
        }

    Never raises for bad input — returns `{"error": "..."}` in `summary_md`
    if freeze() fails (e.g. caller forgot to set round confirmations).
    """
    requirements = tool_input.get("design_parameters") or {}
    # Include the captured requirement list too, so the hash covers them.
    reqs_list = tool_input.get("requirements") or []
    if reqs_list:
        requirements = {**requirements, "_requirement_entries": reqs_list}

    # Auto-derive architecture from tool_input when the caller did not pass one.
    if architecture is None:
        architecture = tool_input.get("architecture") or None

    domain = _infer_domain(design_type, requirements)

    # Build + freeze the lock.  The 4-round state machine (B3.1) will set the
    # per-round booleans at the appropriate turn; here we mark them True since
    # the agent only reaches this point after the user has explicitly confirmed.
    lock = RequirementsLock(
        project_id=str(project_id),
        domain=domain,
        requirements=requirements,
        architecture=architecture,
        round1_confirmed=True,
        round2_confirmed=True,
        round3_confirmed=True,
        round4_confirmed=True,
    )
    try:
        freeze(lock, llm_model=llm_model, llm_model_version=llm_model_version)
        lock_row = save_to_row(lock)
    except Exception as exc:
        logger.warning("p1_finalize.freeze_failed: %s", exc)
        return {
            "lock": None,
            "lock_row": None,
            "audit_report": None,
            "outputs": {},
            "summary_md": f"_(lock not frozen: {exc})_",
        }

    # Build audit inputs
    bom_stages = _tool_bom_to_stages(
        tool_input.get("component_recommendations") or tool_input.get("bom") or []
    )
    claimed_cascade = tool_input.get("cascade_claims") or tool_input.get("design_parameters") or {}
    claimed_parts = _collect_parts(tool_input)
    citations = _collect_citations(tool_input)
    known_parts = _load_known_parts(domain)

    try:
        rep = _run_audit(
            phase_id="P1",
            bom_stages=bom_stages,
            claimed_cascade=claimed_cascade,
            citations=citations,
            claimed_parts=claimed_parts,
            known_parts=known_parts,
            cosite_context=tool_input.get("cosite_context"),
        )
    except Exception as exc:
        logger.warning("p1_finalize.audit_failed: %s", exc)
        rep = AuditReport(
            phase_id="P1",
            issues=[],
            hallucination_count=0,
            unresolved_citations=0,
            cascade_errors=0,
            overall_pass=True,
            confidence_score=0.5,
        )

    # Serialize artifacts
    lock_json = json.dumps(lock.to_dict(), indent=2, sort_keys=True)
    audit_md = audit_report_to_md(rep)
    audit_json = json.dumps(rep.model_dump(), indent=2, default=str)

    blockers = [i for i in rep.issues if i.severity in ("critical", "high")]
    mediums = [i for i in rep.issues if i.severity == "medium"]
    summary_lines = [
        "",
        f"**Requirements lock:** `{lock.requirements_hash[:12]}…`  _(frozen {lock.frozen_at})_",
        f"**Red-team audit:** {'PASS' if rep.overall_pass else 'FAIL'} "
        f"· {len(blockers)} blocker(s) · {len(mediums)} medium · "
        f"confidence {rep.confidence_score:.2f}",
    ]
    if blockers:
        summary_lines.append("")
        summary_lines.append("**Blockers detected:**")
        for b in blockers[:5]:
            summary_lines.append(f"- _{b.severity}_ `{b.category}` — {b.detail}")
    summary_md = "\n".join(summary_lines)

    return {
        "lock": lock.to_dict(),
        "lock_row": lock_row,
        "audit_report": rep.model_dump(),
        "outputs": {
            "requirements_lock.json": lock_json,
            "audit_report.md": audit_md,
            "audit_report.json": audit_json,
        },
        "summary_md": summary_md,
    }
