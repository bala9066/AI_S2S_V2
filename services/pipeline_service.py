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

import asyncio
import json
import logging
import time
from typing import Optional

from config import settings
from services.phase_catalog import AUTO_PHASE_SPECS
from services.project_service import ProjectService
from services.storage import StorageAdapter

log = logging.getLogger(__name__)

# Phase metadata is owned by `services.phase_catalog` so that
# `project_service` and `stale_phases` can reset / audit the same set of
# downstream phases without the three lists drifting apart. We keep the
# `AUTO_PHASES` alias (as a list, so tests can `[p[0] for p in AUTO_PHASES]`)
# for back-compat with call sites that import from here.
AUTO_PHASES = list(AUTO_PHASE_SPECS)


# ─────────────────────────────────────────────────────────────────────────────
# P22 (2026-04-24) — parallel batch execution.
#
# Dependency graph among auto phases (derived from CLAUDE.md phase refs):
#
#   P1 (user-driven) ─┬─> P2  (HRS — needs BOM)
#                      ├─> P4  (Netlist — needs BOM)
#                      └─> P8a (SRS — needs P1..P4)
#   P2 ──────────────> P3  (Compliance — needs BOM + HRS)
#   P4 ──────────────┬─> P6  (GLR — needs netlist)
#                    └─> P8a (also reads netlist)
#   P6 ──────────────> P7  (FPGA RTL — needs GLR)
#   P7 ──────────────> P7a (Register Map — needs FPGA interfaces)
#   P8a ─────────────┬─> P8b (SDD — needs SRS)
#                    └─> P8c (Code Review — needs SRS / source files)
#
# Collapsing into the tightest topological batches that respect every
# dependency while maximising parallelism:
#
#   Batch A:  P2,  P4             (both depend only on P1)
#   Batch B:  P3,  P6,  P8a       (P3→P2, P6→P4, P8a→P4 all done after A)
#   Batch C:  P7,  P8b            (P7→P6, P8b→P8a all done after B)
#   Batch D:  P7a, P8c            (P7a→P7, P8c→P8a all done after C)
#
# Sequential worst case:  ~9 phases × 4 min = ~36 min
# Parallel  worst case:   4 batches × 4 min = ~16 min
#
# CORRECTNESS — no phase runs before every upstream dependency has
# completed. Each phase still gets its full LLM budget; we just issue
# independent phases concurrently via asyncio.gather. No output quality
# is compromised — each phase's agent code is unchanged.
# ─────────────────────────────────────────────────────────────────────────────
_PIPELINE_BATCHES: tuple[tuple[str, ...], ...] = (
    ("P2",  "P4"),
    ("P3",  "P6",  "P8a"),
    ("P7",  "P8b"),
    ("P7a", "P8c"),
)

# Map phase_id → (module_path, class_name, phase_name) for fast lookup.
# Source of truth stays in AUTO_PHASE_SPECS; this is a derived index.
_PHASE_META: dict[str, tuple[str, str, str]] = {
    spec[0]: (spec[1], spec[2], spec[3]) for spec in AUTO_PHASE_SPECS
}


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
        # P22 (2026-04-24): per-project asyncio.Lock to serialise the
        # read-modify-write of the `phase_statuses` JSON column across
        # concurrent phase runs in the same batch. Without this lock,
        # two parallel `async_set_phase_status` calls can race:
        # both read the old JSON, each patches their own phase_id, and
        # whichever commits second clobbers the other's update (classic
        # lost-update). SQLite's default isolation does NOT prevent this
        # on a shared JSON column.
        # The lock is per project_id so unrelated project pipelines
        # never contend; within a single project's pipeline, concurrent
        # phases take the lock only for the duration of a status /
        # output DB write (microseconds) — no LLM-call-time impact.
        self._status_locks: dict[int, asyncio.Lock] = {}

    def _status_lock(self, project_id: int) -> asyncio.Lock:
        """Lazy per-project lock factory — created on first use, reused
        for the lifetime of the PipelineService instance."""
        lock = self._status_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._status_locks[project_id] = lock
        return lock

    async def run_pipeline(self, project_id: int) -> None:
        """Execute auto phases (P2→P8c) in parallel batches respecting
        the dependency graph defined in `_PIPELINE_BATCHES`.

        Batch semantics:
          - Each batch runs via `asyncio.gather(return_exceptions=True)`
            so one failing phase doesn't cancel sibling phases.
          - The NEXT batch starts only after every phase in the current
            batch has terminated (completed or failed).
          - Outputs from every completed phase in a batch are merged
            into `prior_outputs` at the batch boundary, so the next
            batch sees them through the agent's `prior_phase_outputs`.

        Writes phase status to DB per phase using async sessions so the
        FastAPI event loop is never blocked. Designed to be called as:
        `BackgroundTasks.add_task(svc.run_pipeline, project_id)`.
        """
        proj = await self._proj_svc.async_get(project_id)
        if not proj:
            log.error("pipeline.project_not_found", extra={"project_id": project_id})
            return

        log.info(
            "pipeline.started",
            extra={"project_id": project_id, "project_name": proj["name"]},
        )
        prior_outputs: dict[str, str] = self._load_prior_outputs(proj)

        from services.phase_scopes import is_phase_applicable
        scope = (proj.get("design_scope") or "full").lower()
        _pipeline_t0 = time.monotonic()

        for batch_idx, batch in enumerate(_PIPELINE_BATCHES, start=1):
            eligible: list[str] = []
            for phase_id in batch:
                if phase_id not in _PHASE_META:
                    log.warning(
                        "pipeline.phase_unknown_in_batch",
                        extra={"phase": phase_id, "batch": batch_idx},
                    )
                    continue
                if await self._proj_svc.async_get_phase_status(
                    project_id, phase_id,
                ) == "completed":
                    log.info("pipeline.phase_skipped_completed", extra={"phase": phase_id})
                    continue
                if not is_phase_applicable(phase_id, scope):
                    log.info(
                        "pipeline.phase_skipped_out_of_scope",
                        extra={"phase": phase_id, "design_scope": scope},
                    )
                    continue
                eligible.append(phase_id)

            if not eligible:
                continue

            # Refresh project snapshot once per batch (async read, cheap).
            proj = await self._proj_svc.async_get(project_id) or proj

            log.info(
                "pipeline.batch_started",
                extra={
                    "project_id": project_id,
                    "batch": batch_idx,
                    "phases": eligible,
                },
            )
            _batch_t0 = time.monotonic()

            # Fan out this batch. `return_exceptions=True` so one
            # phase's failure doesn't take down sibling phases (match
            # sequential-runner behaviour where we log + continue).
            # A snapshot of `prior_outputs` is passed to each phase so
            # concurrent agents don't race on the same dict instance.
            prior_snapshot = dict(prior_outputs)
            coros = [
                self._run_single_phase(
                    project_id=project_id,
                    proj=proj,
                    phase_id=pid,
                    module_path=_PHASE_META[pid][0],
                    class_name=_PHASE_META[pid][1],
                    phase_name=_PHASE_META[pid][2],
                    prior_outputs=prior_snapshot,
                )
                for pid in eligible
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

            # Merge per-phase outputs into the shared prior_outputs that
            # the NEXT batch will read from. Exceptions were already
            # logged inside _run_single_phase and marked as failed in
            # DB — here we only process successful returns.
            for pid, res in zip(eligible, results):
                if isinstance(res, Exception):
                    log.warning(
                        "pipeline.batch_phase_exception",
                        extra={
                            "project_id": project_id,
                            "phase": pid,
                            "batch": batch_idx,
                            "error": str(res)[:300],
                        },
                    )
                    continue
                if isinstance(res, dict):
                    prior_outputs.update(res)

            log.info(
                "pipeline.batch_completed",
                extra={
                    "project_id": project_id,
                    "batch": batch_idx,
                    "phases": eligible,
                    "duration_s": round(time.monotonic() - _batch_t0, 2),
                },
            )

        log.info(
            "pipeline.completed",
            extra={
                "project_id": project_id,
                "total_duration_s": round(time.monotonic() - _pipeline_t0, 2),
            },
        )

    async def _run_single_phase(
        self,
        project_id: int,
        proj: dict,
        phase_id: str,
        module_path: str,
        class_name: str,
        phase_name: str,
        prior_outputs: dict[str, str],
    ) -> dict[str, str]:
        """Execute one phase. Returns the dict of NEW output files this
        phase wrote (filename → content). Caller merges into the shared
        `prior_outputs` AFTER the enclosing batch completes, so parallel
        sibling phases don't race on a mutable shared dict.

        On agent exception the method catches it, marks the phase failed
        in DB, and returns an empty dict — matching the
        "continue-on-failure" behaviour of the old serial runner.

        `prior_outputs` is READ-ONLY from this method's perspective; a
        caller-provided snapshot is the safe thing to pass when running
        phases concurrently.
        """
        log.info("phase.started", extra={"project_id": project_id, "phase": phase_id})
        # P22: per-project lock serialises all phase_statuses writes so
        # concurrent phases in the same batch don't lose each other's
        # updates. The lock is held ONLY across the SQL round-trip, not
        # across the LLM call — so parallelism is preserved where it
        # matters (LLM thinking) and only the tiny DB write is serialised.
        _lock = self._status_lock(project_id)
        async with _lock:
            await self._proj_svc.async_set_phase_status(project_id, phase_id, "in_progress")
        start = time.monotonic()
        new_outputs: dict[str, str] = {}

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
                    # Collect into the per-phase return value instead of
                    # mutating the shared prior_outputs dict. The
                    # enclosing batch runner merges after all siblings
                    # finish.
                    new_outputs[fname] = result["outputs"][fname]
                    # Ensure content is always a string before writing to DB
                    content_val = result["outputs"][fname]
                    if not isinstance(content_val, str):
                        content_val = json.dumps(content_val, indent=2)
                    # P22: serialise phase-output DB writes too — they
                    # update an updated_at column that might be contested.
                    async with _lock:
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
            async with _lock:
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
            # P22: also under the lock.
            async with _lock:
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

        return new_outputs

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
