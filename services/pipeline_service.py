"""
PipelineService — executes phases as FastAPI background tasks.

Key design decisions:
- All agent execution happens here, NOT in app.py or route handlers.
- Phase status is written to DB immediately (in_progress → completed|failed).
- Background task pattern: caller fires-and-forgets; UI polls /projects/{id}.
- Phase outputs are written through StorageAdapter, never raw Path.write_text().
- All DB writes use async methods so the FastAPI event loop is never blocked.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from config import settings
from services.project_service import ProjectService
from services.storage import StorageAdapter

log = logging.getLogger(__name__)

# Phase metadata: (phase_id, agent_module, agent_class, phase_name)
AUTO_PHASES = [
    ("P2",  "agents.document_agent",   "DocumentAgent",    "HRS Document"),
    ("P3",  "agents.compliance_agent", "ComplianceAgent",  "Compliance"),
    ("P4",  "agents.netlist_agent",    "NetlistAgent",     "Netlist"),
    ("P6",  "agents.glr_agent",        "GLRAgent",         "GLR"),
    ("P7",  "agents.fpga_agent",       "FpgaAgent",        "FPGA RTL Design"),
    ("P7a", "agents.rdt_psq_agent",    "RdtPsqAgent",      "Register Map & Programming Sequence"),
    ("P8a", "agents.srs_agent",        "SRSAgent",         "SRS"),
    ("P8b", "agents.sdd_agent",        "SDDAgent",         "SDD"),
    ("P8c", "agents.code_agent",       "CodeAgent",        "Code + Review"),
]


class PipelineService:
    """
    Manages the P2→P8c automated pipeline.
    Designed to run as a FastAPI BackgroundTask.
    """

    def __init__(
        self,
        project_service: Optional[ProjectService] = None,
        storage: Optional[StorageAdapter] = None,
    ):
        self._proj_svc = project_service or ProjectService()
        self._storage = storage or StorageAdapter.local(settings.output_dir)

    async def run_pipeline(self, project_id: int) -> None:
        """
        Execute all auto phases (P2→P8c) sequentially.
        Writes phase status to DB after every phase using async sessions
        so the FastAPI event loop is never blocked.
        Designed to be called as: BackgroundTasks.add_task(svc.run_pipeline, project_id).
        """
        proj = await self._proj_svc.async_get(project_id)
        if not proj:
            log.error("pipeline.project_not_found", extra={"project_id": project_id})
            return

        log.info("pipeline.started", extra={"project_id": project_id, "project_name": proj["name"]})
        prior_outputs: dict[str, str] = self._load_prior_outputs(proj)

        # Scope gate — honour the wizard-selected design_scope so a "front-end"
        # project doesn't auto-run P6/P7a/P8a-c. Without this, /pipeline/run
        # would silently execute phases the UI correctly marks NOT APPLICABLE.
        from services.phase_scopes import is_phase_applicable
        scope = (proj.get("design_scope") or "full").lower()

        for phase_id, module_path, class_name, phase_name in AUTO_PHASES:
            # Skip already-completed phases (async read)
            if await self._proj_svc.async_get_phase_status(project_id, phase_id) == "completed":
                log.info("pipeline.phase_skipped", extra={"phase": phase_id})
                continue

            if not is_phase_applicable(phase_id, scope):
                log.info(
                    "pipeline.phase_skipped_out_of_scope",
                    extra={"phase": phase_id, "design_scope": scope},
                )
                continue

            await self._run_single_phase(
                project_id=project_id,
                proj=proj,
                phase_id=phase_id,
                module_path=module_path,
                class_name=class_name,
                phase_name=phase_name,
                prior_outputs=prior_outputs,
            )

        log.info("pipeline.completed", extra={"project_id": project_id})

    async def _run_single_phase(
        self,
        project_id: int,
        proj: dict,
        phase_id: str,
        module_path: str,
        class_name: str,
        phase_name: str,
        prior_outputs: dict[str, str],
    ) -> None:
        log.info("phase.started", extra={"project_id": project_id, "phase": phase_id})
        # Async DB write — does not block the event loop
        await self._proj_svc.async_set_phase_status(project_id, phase_id, "in_progress")
        start = time.monotonic()

        try:
            # Lazy-load agent to avoid circular imports + keep startup fast
            import importlib
            module = importlib.import_module(module_path)
            agent_cls = getattr(module, class_name)
            agent = agent_cls()

            # Re-fetch project (async) to get latest state
            proj = await self._proj_svc.async_get(project_id) or proj

            result = await agent.execute(
                project_context={
                    "project_id": project_id,
                    "name": proj["name"],
                    "design_type": proj["design_type"],
                    "output_dir": proj["output_dir"],
                    "design_parameters": proj.get("design_parameters", {}),
                    "prior_phase_outputs": prior_outputs,
                },
                user_input="",
            )

            elapsed = time.monotonic() - start

            # Write outputs through StorageAdapter (sync I/O — acceptable for files)
            if result.get("outputs"):
                written = self._storage.write_outputs(proj["name"], result["outputs"])
                for fname, path in written.items():
                    prior_outputs[fname] = result["outputs"][fname]
                    # Ensure content is always a string before writing to DB
                    content_val = result["outputs"][fname]
                    if not isinstance(content_val, str):
                        content_val = json.dumps(content_val, indent=2)
                    await self._proj_svc.async_record_phase_output(
                        project_id=project_id,
                        phase_id=phase_id,
                        phase_name=phase_name,
                        content=content_val,
                        output_type="markdown",
                        file_path=str(path),
                        model_used=result.get("model_used", ""),
                        tokens_input=result.get("usage", {}).get("input_tokens", 0),
                        tokens_output=result.get("usage", {}).get("output_tokens", 0),
                        duration_seconds=elapsed,
                    )

            # Respect phase_complete flag — if agent signals failure, mark as failed
            # rather than silently completing with no outputs (e.g. P4 tool not called)
            phase_complete = result.get("phase_complete", True)
            final_status = "completed" if phase_complete else "failed"
            await self._proj_svc.async_set_phase_status(
                project_id, phase_id, final_status,
                extra={"duration_seconds": round(elapsed, 2)},
            )
            log.info("phase.completed",
                     extra={"project_id": project_id, "phase": phase_id,
                            "duration_s": round(elapsed, 2)})

        except Exception as exc:
            elapsed = time.monotonic() - start
            log.exception("phase.failed",
                          extra={"project_id": project_id, "phase": phase_id})
            await self._proj_svc.async_set_phase_status(
                project_id, phase_id, "failed",
                extra={"error": str(exc)[:500]},
            )
            await self._proj_svc.async_record_phase_output(
                project_id=project_id,
                phase_id=phase_id,
                phase_name=phase_name,
                content="",
                status="failed",
                error_message=str(exc)[:2000],
                duration_seconds=elapsed,
            )
            # Continue to next phase rather than aborting entire pipeline
            # (allows partial recovery; UI shows which phase failed)

    def _load_prior_outputs(self, proj: dict) -> dict[str, str]:
        """Load all previously-written output files into memory for context."""
        outputs: dict[str, str] = {}
        proj_dir = self._storage.project_dir(proj["name"])
        for f in proj_dir.glob("*.md"):
            try:
                outputs[f.name] = f.read_text(encoding="utf-8")
            except Exception:
                pass
        return outputs

    async def run_single_phase(self, project_id: int, phase_id: str) -> dict:
        """
        Execute one specific phase and return result dict.
        Used by the /phases/{phase_id}/execute endpoint.
        """
        meta = {p[0]: p for p in AUTO_PHASES}
        if phase_id not in meta:
            raise ValueError(f"Unknown phase: {phase_id}")

        _, module_path, class_name, phase_name = meta[phase_id]
        proj = await self._proj_svc.async_get(project_id)
        if not proj:
            raise ValueError(f"Project {project_id} not found")

        prior_outputs = self._load_prior_outputs(proj)
        await self._run_single_phase(
            project_id=project_id,
            proj=proj,
            phase_id=phase_id,
            module_path=module_path,
            class_name=class_name,
            phase_name=phase_name,
            prior_outputs=prior_outputs,
        )
        return await self._proj_svc.async_get(project_id) or {}
