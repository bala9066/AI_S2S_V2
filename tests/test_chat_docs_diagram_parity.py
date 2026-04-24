"""Regression test: chat-draft and persistent-docs Mermaid renderers must
produce the SAME diagram.

User report (2026-04-24):
  > "in chat page why block diagram is same as in documents page block
  >  diagram? it should be same right as in documents page?"

Root cause: `RequirementsAgent._build_response_summary` (the chat-draft
renderer that builds the AI's chat-side summary message) used to read
raw `block_diagram_mermaid` straight from the LLM's tool input — no
salvage, no structured-JSON path — and never even mentioned the
architecture diagram at all. Meanwhile `_generate_output_files` (the
persistent-files writer) routed both `block_diagram` and `architecture`
through `_render_diagram_field`, which prefers the structured spec and
salvages on the raw fallback. Same payload, two different render paths
— chat broke while docs worked.

Fix (P13, 2026-04-24): the chat-draft renderer now also routes through
`_render_diagram_field` for BOTH block_diagram and architecture. These
tests guard the invariant.
"""
from __future__ import annotations

import inspect

import pytest

from agents.requirements_agent import RequirementsAgent


# ---------------------------------------------------------------------------
# Static guards: the chat renderer references _render_diagram_field for
# both block_diagram and architecture, never plucks the raw mermaid.
# ---------------------------------------------------------------------------

def test_response_summary_routes_block_diagram_through_render_diagram_field():
    """Whitebox guard: `_build_response_summary` must call
    `_render_diagram_field` with structured_key='block_diagram'."""
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    assert "_render_diagram_field" in src, (
        "_build_response_summary must route the chat-draft block diagram "
        "through _render_diagram_field so it matches the persistent "
        "docs render path. If you removed the call, also revert P13."
    )
    assert 'structured_key="block_diagram"' in src or "structured_key='block_diagram'" in src


def test_response_summary_routes_architecture_through_render_diagram_field():
    """Whitebox guard: `_build_response_summary` must also render the
    architecture diagram via `_render_diagram_field`. Without this, the
    chat page only showed the block diagram and the user-visible chat
    summary diverged from the persistent `architecture.md` file."""
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    assert 'structured_key="architecture"' in src or "structured_key='architecture'" in src, (
        "_build_response_summary must also render `architecture` via "
        "_render_diagram_field — otherwise the chat page omits the "
        "power-tree / architecture diagram that the docs page shows."
    )


def test_response_summary_does_not_pluck_raw_mermaid_directly():
    """The old code path was `tool_input.get("block_diagram_mermaid")`
    followed by emitting it verbatim into a ```mermaid``` fence. That's
    exactly what produced the broken chat-page diagrams. Block any
    regression that brings it back."""
    src = inspect.getsource(RequirementsAgent._build_response_summary)
    # The old shape: a direct raw read followed by an `if x:` and a fence.
    bad_pattern = 'tool_input.get("block_diagram_mermaid"'
    if bad_pattern in src:
        raise AssertionError(
            "Found direct read of `tool_input.get(\"block_diagram_mermaid\")` "
            "inside `_build_response_summary`. Chat draft must go through "
            "`_render_diagram_field` instead."
        )


# ---------------------------------------------------------------------------
# Behaviour guards: the chat renderer falls back the same way the docs
# renderer does. Use a stub subclass so we can exercise the helpers
# without the agent's full async LLM infrastructure.
# ---------------------------------------------------------------------------

class _StubAgent(RequirementsAgent):
    """Bypass __init__ — we only need the rendering helpers."""

    def __init__(self):  # type: ignore[override]
        # No super().__init__() — RequirementsAgent.__init__ wires LLM
        # clients we don't need here. Build the minimum attribute surface
        # the helpers touch.
        self._offered_candidate_mpns = set()
        self._offered_candidates_by_stage = {}

    def log(self, *_a, **_k):  # silence the salvage info-log
        pass


def test_chat_summary_uses_structured_block_diagram_when_provided():
    """When `block_diagram` (structured spec) is present, the chat draft
    goes through `render_block_diagram` (deterministic, always valid)
    instead of the raw `block_diagram_mermaid` fallback."""
    agent = _StubAgent()
    tool_input = {
        "block_diagram": {
            "direction": "LR",
            "nodes": [
                {"id": "ANT", "label": "Antenna", "shape": "flag"},
                {"id": "LNA", "label": "LNA HMC8410", "shape": "amplifier"},
            ],
            "edges": [{"from": "ANT", "to": "LNA"}],
        },
        # Raw fallback intentionally bad — must NOT be reached.
        "block_diagram_mermaid": (
            'flowchart LR\n    BUCK -- "+5 V" --> LDO\n'
        ),
    }
    md = agent._build_response_summary(tool_input)
    # Block-diagram section is present.
    assert "## System Block Diagram" in md
    # Structured path produced clean Mermaid for the two real nodes.
    assert "ANT" in md and "LNA" in md
    # The bad raw fallback must NOT have leaked through verbatim.
    assert '-- "+5 V" -->' not in md, (
        "structured path should win; raw `block_diagram_mermaid` must "
        "not appear verbatim in the chat draft"
    )


def test_chat_summary_salvages_raw_block_diagram_mermaid_when_structured_missing():
    """When only raw `block_diagram_mermaid` is provided, the chat draft
    must run it through `salvage()` — quoted edge labels become pipes,
    em-dash arrows become `-->`, etc."""
    agent = _StubAgent()
    tool_input = {
        "block_diagram_mermaid": (
            'flowchart TD\n'
            '    BUCK[Buck Conv]\n'
            '    LDO1[LDO Ch1]\n'
            '    BUCK -- "+5 V" --> LDO1\n'
        ),
    }
    md = agent._build_response_summary(tool_input)
    # The salvager fixed the quoted-edge-label syntax.
    assert "BUCK -->|+5 V| LDO1" in md, (
        f"salvager should have converted -- \"+5 V\" --> to -->|+5 V|\n"
        f"got md:\n{md}"
    )
    assert '-- "+5 V" -->' not in md


def test_chat_summary_salvages_raw_architecture_mermaid_when_structured_missing():
    """Architecture diagram (power tree) was the actual user-visible
    failure — `architecture_mermaid` had `BUCK -- "+5 V" --> LDO1` style
    edges that broke Mermaid's parser. Verify salvage rescues it."""
    agent = _StubAgent()
    tool_input = {
        "architecture_mermaid": (
            'flowchart TD\n'
            '    PWR_IN[+28 V MIL Bus Input]\n'
            '    BUCK[Buck Conv BD9F800MUX]\n'
            '    LDO1[LDO Ch1 ADM7170]\n'
            '    PWR_IN -- "+28 V" --> BUCK\n'
            '    BUCK -- "+5 V" --> LDO1\n'
        ),
    }
    md = agent._build_response_summary(tool_input)
    # Architecture section is present.
    assert "## System Architecture" in md
    # All quoted-edge labels converted to pipe form.
    assert '-- "+28 V" -->' not in md
    assert '-- "+5 V" -->' not in md
    assert "PWR_IN -->|+28 V| BUCK" in md
    assert "BUCK -->|+5 V| LDO1" in md


def test_chat_summary_omits_diagram_section_when_no_block_at_all():
    """Empty tool_input (no structured + no raw) → no `## System Block
    Diagram` section in the chat draft. Verifies `allow_empty=True` is
    honored on the chat path."""
    agent = _StubAgent()
    tool_input = {}
    md = agent._build_response_summary(tool_input)
    assert "## System Block Diagram" not in md
    assert "## System Architecture" not in md
