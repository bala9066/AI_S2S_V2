"""P26 #18 (2026-04-26) — regression tests for git_summary.md surfacing
the actual push / PR failure reason.

User report: P8c shipped a `git_summary.md` that read
    Pull Request: Not created (no GitHub remote configured)
EVEN THOUGH `GITHUB_TOKEN` and `GITHUB_REPO` were both set in `.env`.

Root cause: `_push_branch` swallowed the push-side exception and just
returned False. The summary builder couldn't tell whether the failure
was "no remote" (config missing) or "push rejected" (most often the
PAT lacks the `workflow` scope because P8c writes a
`.github/workflows/...` file). Both ended up showing the same generic
"no GitHub remote configured" line.

These tests pin the new contract:
  - `_push_branch` returns `(ok: bool, error_text: str)`.
  - `commit_and_pr` propagates the error text into the result dict
    via `push_error` / `pr_error` / `remote_status`.
  - `code_agent._build_git_summary` renders different bodies per
    `remote_status` so the user can read the ACTUAL reason and the
    fix instruction.
"""
from __future__ import annotations

import pytest

from agents.code_agent import CodeAgent


def _agent() -> CodeAgent:
    """Bypass __init__ so the test doesn't try to load LLM clients /
    static-analysis runners. We only exercise `_build_git_summary`."""
    return CodeAgent.__new__(CodeAgent)


# ---------------------------------------------------------------------------
# Happy path: PR url present
# ---------------------------------------------------------------------------


def test_summary_shows_pr_url_when_present():
    md = _agent()._build_git_summary({
        "success": True,
        "commit_sha": "deadbeef00",
        "branch": "ai/x",
        "pr_url": "https://github.com/foo/bar/pull/42",
    }, "TestProj")
    assert "Committed" in md
    assert "deadbeef00" in md
    assert "ai/x" in md
    assert "https://github.com/foo/bar/pull/42" in md
    # The misleading legacy line MUST be gone.
    assert "no GitHub remote configured" not in md


# ---------------------------------------------------------------------------
# Push failed: workflow-scope error must surface verbatim + fix instruction
# ---------------------------------------------------------------------------


def test_summary_surfaces_workflow_scope_push_error():
    md = _agent()._build_git_summary({
        "success": True,
        "commit_sha": "deadbeef00",
        "branch": "ai/x",
        "pr_url": None,
        "remote_status": "push_failed",
        "push_error": (
            "remote: error: GH013: Refusing to allow a Personal Access "
            "Token to create or update workflow `.github/workflows/"
            "hardware_pipeline_ci.yml` without `workflow` scope. — your "
            "GITHUB_TOKEN is missing the `workflow` scope. Re-issue the "
            "PAT with both `repo` AND `workflow` scopes ticked."
        ),
        "pr_error": "",
    }, "TestProj")
    # The actual GitHub error text must appear so the user sees what
    # GitHub said.
    assert "workflow" in md
    assert "scope" in md
    # The actionable fix instruction must appear.
    assert "Re-issue the PAT" in md or "workflow` scope" in md
    # Must NOT show the misleading legacy line.
    assert "no GitHub remote configured" not in md
    # Status must read "push to GitHub failed" so the user knows it
    # wasn't a config problem.
    assert "push to GitHub failed" in md


# ---------------------------------------------------------------------------
# No remote configured: still actionable, NOT misleading
# ---------------------------------------------------------------------------


def test_summary_no_remote_when_repo_blank():
    md = _agent()._build_git_summary({
        "success": True,
        "commit_sha": "deadbeef00",
        "branch": "ai/x",
        "pr_url": None,
        "remote_status": "no_remote",
        "push_error": "GITHUB_REPO not set in .env",
    }, "TestProj")
    assert "GITHUB_REPO not set" in md
    # Must direct the user to the .env fix.
    assert ".env" in md
    # Must mention the workflow scope requirement so the next attempt
    # doesn't hit the workflow-scope error.
    assert "workflow" in md and "scope" in md


# ---------------------------------------------------------------------------
# Push OK but PR creation failed
# ---------------------------------------------------------------------------


def test_summary_push_ok_pr_failed():
    md = _agent()._build_git_summary({
        "success": True,
        "commit_sha": "deadbeef00",
        "branch": "ai/x",
        "pr_url": None,
        "remote_status": "push_ok_pr_failed",
        "push_error": "",
        "pr_error": "branch protection blocks PR creation",
    }, "TestProj")
    assert "Push succeeded but PR creation failed" in md
    assert "branch protection blocks PR creation" in md
    # The user should know the branch IS on GitHub already.
    assert "manually" in md or "GitHub UI" in md


# ---------------------------------------------------------------------------
# Sanity: failed-status case (commit didn't even happen)
# ---------------------------------------------------------------------------


def test_summary_failed_status_renders_reason():
    md = _agent()._build_git_summary({
        "success": False,
        "reason": "Git integration disabled — set GITHUB_TOKEN in .env",
    }, "TestProj")
    assert "Git skipped" in md
    assert "GITHUB_TOKEN" in md


# ---------------------------------------------------------------------------
# git_agent._push_branch returns a tuple now
# ---------------------------------------------------------------------------


def test_push_branch_returns_tuple_signature():
    """The new (ok, err) tuple signature is what unblocks the summary —
    a regression that returns a bool would silently break the
    push_error propagation path. Pin the signature here."""
    from agents.git_agent import GitAgent
    import inspect
    sig = inspect.signature(GitAgent._push_branch)
    # Return annotation must be tuple[bool, str].
    ret = sig.return_annotation
    # `tuple[bool, str]` shows up as `tuple[bool, str]` (PEP 585) or
    # `Tuple[bool, str]`. Accept either by stringifying.
    assert "tuple" in str(ret).lower(), (
        f"_push_branch must return a tuple (got {ret}) so commit_and_pr "
        f"can surface the actual push error in git_summary.md"
    )
