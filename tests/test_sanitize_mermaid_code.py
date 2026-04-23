"""Regression tests for `main._sanitize_mermaid_code`.

The DOCX export path sanitises every ```mermaid``` block before shipping it to
`_render_mermaid_local` (mermaid.ink → mmdc → node bundled renderer).  When
sanitisation corrupts the input, all 3 backends fail and the export falls
back to a "(rendered in browser — source below)" placeholder — the user-
facing symptom is a DOCX with a heading and no image.

This module guards the specific parser failure that sent a real 12 GHz Rx
block diagram into the fallback: round-bracket nodes whose quoted label
contained nested parens.  Mirrors the frontend tests in
`hardware-pipeline-v5-react/src/utils/mermaidSanitize.test.ts`.
"""
from __future__ import annotations

import importlib
import re


_main = importlib.import_module("main")
sanitize = _main._sanitize_mermaid_code  # noqa: SLF001


class TestRoundBracketNestedParens:

    def test_vga_agc_node_collapses_to_square_brackets(self):
        """Regression: the exact line from the 12 GHz Rx diagram."""
        src = 'flowchart LR\n    S11("VGA (AGC)<br/>HMC624LP4E")'
        out = sanitize(src)
        # The sanitiser should produce a single well-formed node, not a
        # mangled `S11(..)` followed by floating `HMC624LP4E")` garbage.
        assert re.search(r'S11\[[^\]]*VGA[^\]]*AGC[^\]]*HMC624LP4E[^\]]*\]', out), (
            f"expected S11[... VGA ... AGC ... HMC624LP4E ...], got:\n{out}"
        )
        # No unmatched-close-paren leftover on the line.
        assert '")' not in out, f"leftover '\") in output:\n{out}"
        assert '"' not in out, f"leftover stray quote in output:\n{out}"

    def test_plain_round_bracket_node_keeps_shape(self):
        """A round-bracket node without nested parens keeps the round shape."""
        src = 'flowchart LR\n    S4("LNA Stage 1<br/>HMC618ALP3E")'
        out = sanitize(src)
        # No inner parens → still a round-bracket node.
        assert 'S4(' in out, f"round-bracket node should be preserved:\n{out}"
        assert 'HMC618ALP3E' in out

    def test_full_rx_front_end_chain_renders_cleanly(self):
        """End-to-end: the shape the pipeline actually emits for a 12 GHz Rx."""
        src = (
            'flowchart LR\n'
            '    %% 12.00 GHz +- 50 MHz\n'
            '    ANT((Antenna)) --> S1\n'
            '    S1["N-type Input Connector<br/>N-type IP67 50 ohm"]\n'
            '    S2["PCB Trace<br/>50Ohm Microstrip (RO4350B)"]\n'
            '    S11("VGA (AGC)<br/>HMC624LP4E")\n'
            '    S1 --> S2\n'
            '    S2 --> S11\n'
        )
        out = sanitize(src)
        # Diagram type survives.
        assert out.lstrip().startswith('flowchart LR'), out
        # Edges survive intact.
        assert re.search(r'S1\s*-->\s*S2', out), out
        assert re.search(r'S2\s*-->\s*S11', out), out
        # Both problem nodes land as square-bracket nodes with their MPN.
        assert re.search(r'S2\[[^\]]*RO4350B[^\]]*\]', out), out
        assert re.search(r'S11\[[^\]]*HMC624LP4E[^\]]*\]', out), out
        # No floating garbage from the broken sanitiser.
        assert 'HMC624LP4E")' not in out
