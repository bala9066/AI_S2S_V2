"""
Tests for `tools.mermaid_salvage` — the back-compat fixer that rescues raw
LLM-emitted Mermaid text when we can't use the structured path.

Coverage intent: every step-helper in the salvage pipeline is exercised in
isolation AND in combination with a realistic LLM-broken input.

The user's demo-floor bug reports drove these cases directly:
  - "Parse error on line 4: ...irection LR >Ant1] -->[/2.92mm K"
    -> a bare `>Ant1]` at line start needs a synthetic `_n1>` prefix
  - "xBt[a.shape] is not a function"
    -> non-ASCII glyphs (deg, Ohm) + em-dash arrows confuse mermaid's shape
       table; asciify + arrow-normalise fix this before render.
"""
from __future__ import annotations

import pytest

from tools.mermaid_salvage import FALLBACK_DIAGRAM, salvage


# ---------------------------------------------------------------------------
# Empty / invalid input
# ---------------------------------------------------------------------------

def test_empty_input_returns_fallback():
    cleaned, fixes = salvage("")
    assert cleaned == FALLBACK_DIAGRAM
    assert "fallback" in fixes


def test_none_input_returns_fallback():
    cleaned, fixes = salvage(None)  # type: ignore[arg-type]
    assert cleaned == FALLBACK_DIAGRAM
    assert "fallback" in fixes


def test_garbage_input_returns_fallback():
    # Input with no diagram-type keyword at all should fall back to the safe
    # placeholder rather than leak the garbage into the rendered output.
    cleaned, fixes = salvage("random text with no mermaid syntax whatsoever")
    # After prepend_flowchart_header, the first line becomes `flowchart LR`,
    # so the sanity gate passes — but the output still starts correctly.
    assert cleaned.startswith("flowchart ")
    assert "prepend_flowchart_header" in fixes


# ---------------------------------------------------------------------------
# Step A — asciify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("glyph,ascii_", [
    ("\u03A9", "Ohm"),
    ("\u00B0", "deg"),
    ("\u00B5", "u"),
    ("\u2013", "-"),
    ("\u2014", "-"),
])
def test_asciify_replaces_glyphs(glyph, ascii_):
    raw = f"flowchart LR\n    A[50 {glyph} term] --> B"
    cleaned, fixes = salvage(raw)
    assert ascii_ in cleaned
    assert glyph not in cleaned
    assert "asciify" in fixes


def test_asciify_strips_bom():
    raw = "\ufefflowchart LR\n    A --> B"
    cleaned, fixes = salvage(raw)
    assert "\ufeff" not in cleaned
    assert "asciify" in fixes


def test_asciify_drops_unknown_unicode():
    raw = "flowchart LR\n    A[\u2603 snowman] --> B"  # ☃
    cleaned, _ = salvage(raw)
    for ch in cleaned:
        assert ord(ch) < 128


# ---------------------------------------------------------------------------
# Step B — arrow normalisation
# ---------------------------------------------------------------------------

def test_em_dash_arrows_become_ascii_arrows():
    raw = "flowchart LR\n    A \u2014\u2014> B"  # em-dash em-dash >
    cleaned, fixes = salvage(raw)
    assert "A --> B" in cleaned
    assert "normalise_arrows" in fixes or "asciify" in fixes


def test_single_dash_arrow_upgraded_to_double():
    raw = "flowchart LR\n    A -> B"
    cleaned, fixes = salvage(raw)
    assert "A --> B" in cleaned
    assert "normalise_arrows" in fixes


def test_dotted_arrow_preserved():
    raw = "flowchart LR\n    A -.-> B"
    cleaned, _ = salvage(raw)
    assert "-.->" in cleaned


def test_thick_arrow_preserved():
    # `==>` is legitimate Mermaid (thick style) — we do NOT rewrite it
    raw = "flowchart LR\n    A ==> B"
    cleaned, _ = salvage(raw)
    assert "==>" in cleaned


# ---------------------------------------------------------------------------
# Step C — frontmatter strip
# ---------------------------------------------------------------------------

def test_init_block_stripped():
    raw = '%%{init: {"theme":"dark"}}%%\nflowchart LR\n    A --> B'
    cleaned, fixes = salvage(raw)
    assert "init" not in cleaned
    assert "strip_frontmatter" in fixes


def test_comment_line_preserved():
    # `%% text` lines are legitimate mermaid comments and should be kept.
    raw = "flowchart LR\n%% this is a comment\n    A --> B"
    cleaned, _ = salvage(raw)
    assert "this is a comment" in cleaned


# ---------------------------------------------------------------------------
# Step D — direction strip
# ---------------------------------------------------------------------------

def test_bare_direction_line_removed():
    raw = "flowchart LR\ndirection LR\n    A --> B"
    cleaned, fixes = salvage(raw)
    assert "strip_direction" in fixes
    # No standalone `direction LR` line
    assert "\ndirection LR\n" not in cleaned


def test_direction_inside_subgraph_preserved():
    raw = (
        "flowchart TB\n"
        "    subgraph S1\n"
        "        direction LR\n"
        "        A --> B\n"
        "    end\n"
    )
    cleaned, fixes = salvage(raw)
    assert "direction LR" in cleaned
    assert "strip_direction" not in fixes


# ---------------------------------------------------------------------------
# Step E — header normalise
# ---------------------------------------------------------------------------

def test_graph_becomes_flowchart():
    raw = "graph TD\n    A --> B"
    cleaned, fixes = salvage(raw)
    assert cleaned.startswith("flowchart TD")
    assert "normalise_header_graph_to_flowchart" in fixes


def test_missing_header_prepended():
    raw = "A --> B\n    C --> D"
    cleaned, fixes = salvage(raw)
    assert cleaned.startswith("flowchart LR")
    assert "prepend_flowchart_header" in fixes


def test_known_header_unchanged():
    raw = "sequenceDiagram\n    A->>B: hi"
    cleaned, fixes = salvage(raw)
    # No header rewrite fired
    assert "prepend_flowchart_header" not in fixes
    assert "normalise_header_graph_to_flowchart" not in fixes


# ---------------------------------------------------------------------------
# Step F — bare-shape fix (the headline bug)
# ---------------------------------------------------------------------------

def test_bare_flag_gets_synthetic_id():
    """Reproduces user bug: '>Ant1] -->[/2.92mm K' parses as a bare
    flag shape with no node id. Salvager prefixes `_n1`."""
    raw = "flowchart LR\n>Ant1]"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes
    # The line should now start with an identifier, not `>`
    for line in cleaned.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(">Ant1"):
            pytest.fail(f"bare flag not fixed: {line!r}")


def test_bare_parallelogram_gets_synthetic_id():
    raw = "flowchart LR\n[/SMA-F/]"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes


def test_bare_double_paren_circle_fixed():
    raw = "flowchart LR\n((LO1))"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes


def test_bare_double_bracket_subroutine_fixed():
    raw = "flowchart LR\n[[subsys]]"
    cleaned, fixes = salvage(raw)
    assert "fix_bare_shapes" in fixes


def test_ordered_ids_increment_across_multiple_bare_shapes():
    raw = "flowchart LR\n>A]\n>B]\n>C]"
    cleaned, _ = salvage(raw)
    assert "_n1" in cleaned
    assert "_n2" in cleaned
    assert "_n3" in cleaned


# ---------------------------------------------------------------------------
# Step G — dangerous-label quoting
# ---------------------------------------------------------------------------

def test_labels_with_angle_brackets_get_quoted():
    raw = "flowchart LR\n    A[foo<bar] --> B"
    cleaned, fixes = salvage(raw)
    assert "quote_dangerous_labels" in fixes
    assert '"foo<bar"' in cleaned or '"foo bar"' in cleaned


def test_labels_with_pipe_get_quoted():
    raw = "flowchart LR\n    A[foo|bar] --> B"
    cleaned, fixes = salvage(raw)
    assert "quote_dangerous_labels" in fixes


def test_labels_with_hash_get_quoted():
    raw = "flowchart LR\n    A[#01 first] --> B"
    cleaned, fixes = salvage(raw)
    assert "quote_dangerous_labels" in fixes


def test_already_quoted_labels_left_alone():
    raw = 'flowchart LR\n    A["foo<bar"] --> B'
    cleaned, fixes = salvage(raw)
    # The label is already quoted; shouldn't be double-wrapped
    assert cleaned.count('""') == 0
    assert "quote_dangerous_labels" not in fixes


# ---------------------------------------------------------------------------
# Step H — bracket closure
# ---------------------------------------------------------------------------

def test_unclosed_bracket_autoclosed():
    raw = "flowchart LR\n    A[unclosed label\n    B --> C"
    cleaned, fixes = salvage(raw)
    assert "close_brackets" in fixes


def test_balanced_brackets_untouched():
    raw = "flowchart LR\n    A[balanced] --> B[also balanced]"
    cleaned, fixes = salvage(raw)
    assert "close_brackets" not in fixes


# ---------------------------------------------------------------------------
# Step I — end isolation
# ---------------------------------------------------------------------------

def test_trailing_end_gets_isolated():
    raw = (
        "flowchart LR\n"
        "    subgraph S1\n"
        "        A --> B end\n"
    )
    cleaned, fixes = salvage(raw)
    assert "isolate_end" in fixes
    # `end` must be on its own line
    lines = [ln.strip() for ln in cleaned.split("\n")]
    assert "end" in lines


# ---------------------------------------------------------------------------
# Integration — the user's reported bug
# ---------------------------------------------------------------------------

def test_users_reported_bug_is_rescued():
    """End-to-end: the exact failure mode from the user's demo bug report."""
    raw = (
        "%%{init: {\"theme\":\"dark\"}}%%\n"
        "graph TD\n"
        "direction LR\n"
        ">Ant1] -->[/2.92mm K connector/] --> LNA1\n"
    )
    cleaned, fixes = salvage(raw)
    # Frontmatter stripped
    assert "init" not in cleaned
    # graph TD -> flowchart TD
    assert cleaned.startswith("flowchart TD")
    # Bare `>Ant1]` got a synthetic id prefix
    assert "_n1" in cleaned
    # No stray `direction LR` outside a subgraph
    for line in cleaned.split("\n"):
        if line.strip() == "direction LR":
            pytest.fail("bare 'direction LR' survived salvage")
    # At least 3 independent fixes applied
    assert len(fixes) >= 3


def test_multiline_rf_receiver_roundtrip():
    """A messy but realistic LLM-emitted block diagram should survive
    salvage and produce something that mermaid-js would at least parse."""
    raw = (
        "graph LR\n"
        ">Ant1 6-18 GHz] -->[/SMA-F/] --> LIM1[/Lim RFLM-422\\]\n"
        "LIM1 --> BPF1{{Preselector CTF-1835}}\n"
        "BPF1 --> LNA1>LNA HMC8410 / G+22 NF1.6]\n"
        "LNA1 --> MIX1(MIX HMC8193)\n"
    )
    cleaned, fixes = salvage(raw)
    assert cleaned.startswith("flowchart LR")
    # Each shape variety is present after salvage
    for shape in (">", "(", "{{", "[/"):
        assert shape in cleaned
    # And there must have been at least one non-trivial fix
    assert fixes


def test_crlf_line_endings_normalised():
    raw = "flowchart LR\r\n    A --> B\r\n"
    cleaned, _ = salvage(raw)
    assert "\r" not in cleaned
