"""
Tests for services/pipeline_service.py — the P2→P8c auto-run executor.

Strategy:
- Patch each agent module so `execute()` returns a deterministic output
  dict, then assert on DB side-effects (phase_statuses, phase_outputs,
  conversation_history).
- Pipeline uses async session writes, so the fixture disposes the async
  engine on teardown to avoid aiosqlite ResourceWarnings (tests run with
  filterwarnings=error).
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def pipe_env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "pipe.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

    import config as _config
    importlib.reload(_config)
    import database.models as _models
    _models._engine = None
    _models._SessionLocal = None
    _models._async_engine = None
    _models._AsyncSessionLocal = None
    _models._resolved_db_url = None
    importlib.reload(_models)
    import services.storage as _storage
    importlib.reload(_storage)
    import services.project_service as _ps
    importlib.reload(_ps)
    import services.pipeline_service as _pl
    importlib.reload(_pl)

    from database.models import get_engine as _force_init
    _force_init()
    from services.project_service import ProjectService
    from services.pipeline_service import PipelineService
    proj_svc = ProjectService()
    pipe_svc = PipelineService(project_service=proj_svc)

    yield proj_svc, pipe_svc, tmp_path

    import asyncio
    try:
        if _models._async_engine is not None:
            asyncio.get_event_loop().run_until_complete(
                _models._async_engine.dispose()
            )
    except Exception:
        pass
    try:
        if _models._engine is not None:
            _models._engine.dispose()
    except Exception:
        pass


def _stub_agent(response_text: str = "ok", filename: str = "out.md",
                phase_complete: bool = True):
    agent = MagicMock()
    agent.execute = AsyncMock(return_value={
        "response": response_text,
        "phase_complete": phase_complete,
        "outputs": {filename: f"# {filename}\n\n{response_text}\n"},
        "model_used": "stub-model",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    })
    return agent


def _patch_all_phase_agents(phase_complete: bool = True):
    """Context-manager stack that patches every AUTO_PHASES agent."""
    from services.pipeline_service import AUTO_PHASES
    patches = []
    for phase_id, module_path, class_name, _ in AUTO_PHASES:
        agent_cls_mock = MagicMock(
            return_value=_stub_agent(
                filename=f"{phase_id.lower()}.md",
                phase_complete=phase_complete,
            )
        )
        patches.append(patch(f"{module_path}.{class_name}", agent_cls_mock))
    return patches


class _Stacked:
    def __init__(self, patches):
        self._patches = patches
    def __enter__(self):
        for p in self._patches:
            p.start()
        return self
    def __exit__(self, *a):
        for p in self._patches:
            p.stop()


# ---------------------------------------------------------------------------
# run_pipeline — full P2→P8c happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_pipeline_completes_all_auto_phases(pipe_env):
    proj_svc, pipe_svc, tmp_path = pipe_env
    proj = proj_svc.create(name="FullRun")
    # Pre-create project dir so storage adapter has somewhere to write
    (tmp_path / "output" / "fullrun").mkdir(parents=True, exist_ok=True)

    with _Stacked(_patch_all_phase_agents()):
        await pipe_svc.run_pipeline(proj["id"])

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    from services.pipeline_service import AUTO_PHASES
    for phase_id, *_ in AUTO_PHASES:
        assert statuses.get(phase_id, {}).get("status") == "completed", (
            f"{phase_id} did not complete: {statuses.get(phase_id)}"
        )


@pytest.mark.asyncio
async def test_run_pipeline_skips_already_completed_phases(pipe_env):
    """If P2 is already marked completed, its agent must not be re-invoked."""
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="SkipDone")
    proj_svc.set_phase_status(proj["id"], "P2", "completed")

    # Patch P2's agent with a MagicMock that records invocations.
    p2_agent = _stub_agent(filename="p2.md")
    p2_cls = MagicMock(return_value=p2_agent)

    patches = _patch_all_phase_agents()
    patches.append(patch("agents.document_agent.DocumentAgent", p2_cls))
    with _Stacked(patches):
        await pipe_svc.run_pipeline(proj["id"])

    p2_agent.execute.assert_not_called()


@pytest.mark.asyncio
async def test_run_single_phase_completes_target_phase_only(pipe_env):
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="SinglePhase")

    with _Stacked(_patch_all_phase_agents()):
        await pipe_svc.run_single_phase(proj["id"], "P2")

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    assert statuses["P2"]["status"] == "completed"
    # Other phases must stay pending
    assert "P3" not in statuses or statuses["P3"]["status"] == "pending"


@pytest.mark.asyncio
async def test_run_single_phase_unknown_phase_raises_value_error(pipe_env):
    _, pipe_svc, _ = pipe_env
    with pytest.raises(ValueError, match="Unknown phase"):
        await pipe_svc.run_single_phase(1, "P99")


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_exception_marks_phase_failed_but_pipeline_continues(pipe_env):
    """An agent that throws must not abort the pipeline — the phase flips
    to 'failed' and the next phase still runs."""
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="ContinueOnFail")

    # P2 blows up, P3 succeeds
    crash_agent = MagicMock()
    crash_agent.execute = AsyncMock(side_effect=RuntimeError("boom"))
    crash_cls = MagicMock(return_value=crash_agent)

    patches = _patch_all_phase_agents()
    patches.append(patch("agents.document_agent.DocumentAgent", crash_cls))
    with _Stacked(patches):
        await pipe_svc.run_pipeline(proj["id"])

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    assert statuses["P2"]["status"] == "failed"
    assert statuses["P3"]["status"] == "completed"


@pytest.mark.asyncio
async def test_agent_phase_complete_false_marks_phase_failed(pipe_env):
    """If agent returns phase_complete=False, the phase is marked failed —
    not silently completed with empty outputs."""
    proj_svc, pipe_svc, _ = pipe_env
    proj = proj_svc.create(name="SignalFailure")

    unhappy_agent = _stub_agent(phase_complete=False, filename="p2.md")
    patches = _patch_all_phase_agents()
    patches.append(patch(
        "agents.document_agent.DocumentAgent",
        MagicMock(return_value=unhappy_agent),
    ))
    with _Stacked(patches):
        await pipe_svc.run_single_phase(proj["id"], "P2")

    statuses = proj_svc.get(proj["id"])["phase_statuses"]
    assert statuses["P2"]["status"] == "failed"


# ---------------------------------------------------------------------------
# Auto-phases table shape — guards the P5/P7 manual exclusion
# ---------------------------------------------------------------------------

def test_auto_phases_excludes_manual_p5_and_p7_does_include_all_ai_phases():
    from services.pipeline_service import AUTO_PHASES
    phase_ids = [p[0] for p in AUTO_PHASES]
    # P5 (PCB layout) and P1 (P1 is driven by chat, not pipeline) must NOT be here
    assert "P1" not in phase_ids
    assert "P5" not in phase_ids
    # Every auto AI phase must be here
    assert set(phase_ids) == {"P2", "P3", "P4", "P6", "P7", "P7a", "P8a", "P8b", "P8c"}
