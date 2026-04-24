"""
Back-compat Mermaid salvager — for raw LLM-emitted text we still accept.

New code should emit structured `BlockDiagramSpec` and pass through
`tools.mermaid_render.render_block_diagram()` — output is then valid by
construction. This module exists for:

  1. `architecture_mermaid` and other free-form fields we haven't migrated
     yet.
  2. Legacy DB rows whose `block_diagram_mermaid` is raw Mermaid text.
  3. The P1 finalize retry loop, where the LLM sometimes re-emits raw
     Mermaid on a retry even after we ask for structured output.

Salvage strategy (applied in order, each step independent):

  A. Unicode ASCIIfication — Ohm/deg/u/em-dash/arrows → ASCII equivalents.
  B. Arrow normalisation — `==>`, `->`, `——>`, unicode arrows → `-->`.
  C. Frontmatter strip — `%%{init ...}%%`, `%% comments`, BOM.
  D. Direction scrub — `direction LR` at top level → removed (belongs in
     subgraph only).
  E. Diagram-type fixup — `graph TD` → `flowchart TD`; ensure `flowchart X`
     is on line 1.
  F. Bare-shape IDs — `>Ant1]` starting a line → auto-prefix `_n0>"Ant1"]`.
  G. Label quoting — wrap any node `[...]`/`(...)`/`{{...}}` label that
     contains punctuation the tokeniser dislikes (`<>"#|`) in `"..."`.
  H. Bracket balancing — auto-close unclosed `[` at end of line.
  I. `end` keyword isolation — ensure `subgraph ... end` closes on its
     own line.
  J. Final sanity gate — if we still can't find `flowchart` on line 1,
     fall back to a placeholder diagram.

The function is pure — no I/O, no side effects — and returns both the
cleaned text AND the list of fixes applied so callers can log what was
rescued. That log is critical for diagnosing LLM regressions: if we see
the same fix fire 100 times/day, the prompt needs tightening.

Public API:
    salvage(raw: str) -> tuple[str, list[str]]
    FALLBACK_DIAGRAM       — the safe minimal diagram used as last resort
"""
from __future__ import annotations

import re
from typing import Optional

__all__ = ["salvage", "FALLBACK_DIAGRAM"]


# The placeholder we emit when nothing else works — always parses, always
# renders, explicitly tells the user something went wrong.
FALLBACK_DIAGRAM = (
    "flowchart LR\n"
    '    ERR["diagram could not be rendered"]\n'
    '    HINT["ask P1 to regenerate the block diagram"]\n'
    "    ERR --> HINT\n"
)


# Non-ASCII → ASCII mapping. Same table as mermaid_render; duplicated here
# to keep the salvager a standalone module that doesn't depend on the
# renderer's internals.
_NON_ASCII_MAP: dict[str, str] = {
    "\u03A9": "Ohm", "\u2126": "Ohm",
    "\u00B0": "deg", "\u00B5": "u",
    "\u2013": "-", "\u2014": "-",
    "\u2018": "'", "\u2019": "'",
    "\u201C": "'", "\u201D": "'",
    "\u2264": "<=", "\u2265": ">=",
    "\u00B1": "+-",
    "\u2192": "-->", "\u2190": "<--",
    "\u2022": "*", "\u00B7": "*",
    "\ufeff": "",  # BOM
}

# Shape-opening tokens. Order matters: longer tokens first so we don't
# match `[` when `[[` or `[/` is present.
_SHAPE_OPENERS: tuple[str, ...] = (
    "[[", "[/", "[\\", "[(",  # 2-char brackets
    "{{",                      # hexagon
    "((",                      # circle
    "[", "{", "(", ">",        # 1-char
)

_DIAGRAM_TYPES: tuple[str, ...] = (
    "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "gantt", "pie", "gitgraph", "mindmap", "timeline",
    "journey", "quadrantchart", "requirementdiagram", "c4context",
)


# ---------------------------------------------------------------------------
# Step helpers — each returns (text, fix_applied_or_None)
# ---------------------------------------------------------------------------

def _step_asciify(text: str) -> tuple[str, Optional[str]]:
    """Step A — replace non-ASCII glyphs with ASCII equivalents."""
    original = text
    for glyph, repl in _NON_ASCII_MAP.items():
        text = text.replace(glyph, repl)
    # Drop any remaining non-ASCII as a safety net.
    text2 = "".join(ch if ord(ch) < 128 else "" for ch in text)
    if text2 != original:
        return text2, "asciify"
    return text, None


def _step_normalise_arrows(text: str) -> tuple[str, Optional[str]]:
    """Step B — `==>`, `->`, `——>` → `-->`. Preserves `-.->` (dotted)
    because that's a legitimate Mermaid form we don't want to flatten."""
    original = text
    # `==>` (thick arrow) is valid Mermaid but often emitted where plain
    # arrow was meant. Leave it alone — the renderer uses `==>` for style=thick.
    # Single `->` is NOT valid — upgrade.
    text = re.sub(r"(?<![-=.])->(?!>)", "-->", text)
    # Em-dash arrows.
    text = re.sub(r"—+>", "-->", text)
    if text != original:
        return text, "normalise_arrows"
    return text, None


def _step_strip_frontmatter(text: str) -> tuple[str, Optional[str]]:
    """Step C — drop `%%{init ...}%%` and `%% comment` lines."""
    original = text
    text = re.sub(r"%%\{[\s\S]*?\}%%\s*", "", text)
    # Strip line-comments but preserve our own `%% ERROR:` markers from
    # the renderer's soft-error mode (they're still valid Mermaid comments).
    if text != original:
        return text, "strip_frontmatter"
    return text, None


def _step_strip_direction(text: str) -> tuple[str, Optional[str]]:
    """Step D — remove bare `direction LR` lines that are outside a
    subgraph. Inside a subgraph Mermaid does accept them; we only strip
    lines where the preceding non-blank line isn't `subgraph`."""
    lines = text.split("\n")
    out: list[str] = []
    changed = False
    inside_subgraph = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("subgraph "):
            inside_subgraph = True
            out.append(line)
            continue
        if stripped == "end":
            inside_subgraph = False
            out.append(line)
            continue
        if (
            re.match(r"^\s*direction\s+(LR|TD|TB|RL|BT)\s*$", line)
            and not inside_subgraph
        ):
            changed = True
            continue  # drop this line
        out.append(line)
    return ("\n".join(out), "strip_direction" if changed else None)


def _step_normalise_header(text: str) -> tuple[str, Optional[str]]:
    """Step E — ensure the first non-blank line starts with a valid
    diagram type (`flowchart LR`, etc.). If it says `graph X`, upgrade to
    `flowchart X`. If it's missing entirely, prepend `flowchart LR`."""
    lines = text.split("\n")
    # Find first non-blank line.
    first_idx = next((i for i, ln in enumerate(lines) if ln.strip()), 0)
    first = lines[first_idx].strip().lower()

    if first.startswith("graph "):
        lines[first_idx] = re.sub(
            r"^\s*graph\s+", "flowchart ", lines[first_idx], count=1,
        )
        return "\n".join(lines), "normalise_header_graph_to_flowchart"

    if first.startswith(_DIAGRAM_TYPES):
        return text, None

    # Prepend a sensible default.
    return "flowchart LR\n" + text, "prepend_flowchart_header"


def _step_fix_bare_shapes(text: str) -> tuple[str, Optional[str]]:
    """Step F — `>Ant1]`, `[/SMA/]`, `((Circle))` starting a line (no
    preceding node-id token) → prefix with a synthetic id `_n{i}` so
    Mermaid's parser accepts it."""
    lines = text.split("\n")
    changed = False
    synth_idx = 0
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        # Match any shape opener at the start.
        matched = False
        for opener in _SHAPE_OPENERS:
            if stripped.startswith(opener):
                synth_idx += 1
                out.append(f"{indent}_n{synth_idx}{stripped}")
                changed = True
                matched = True
                break
        if not matched:
            out.append(line)
    return ("\n".join(out), "fix_bare_shapes" if changed else None)


def _step_quote_dangerous_labels(text: str) -> tuple[str, Optional[str]]:
    """Step G — find node-label contents that contain tokeniser-hostile
    chars (`<`, `>`, `#`, `|`, pipe, unescaped `(`) and wrap in `"..."`.

    We only touch labels inside `[...]` / `(...)` / `{{...}}` / `{...}` /
    `[/.../]` / `[\\...\\]` / `>...]`. To avoid double-wrapping, we skip
    labels that are already quoted."""
    changed_any = False

    # Each pattern captures (open_delim, inner, close_delim).
    patterns: tuple[tuple[re.Pattern[str], str, str], ...] = (
        (re.compile(r"(\[\[)([^\]\[]*?)(\]\])"), "[[", "]]"),
        (re.compile(r"(\{\{)([^}{]*?)(\}\})"), "{{", "}}"),
        (re.compile(r"(\(\()([^)(]*?)(\)\))"), "((", "))"),
        (re.compile(r"(\[/)([^/\]]*?)(/\])"), "[/", "/]"),
        (re.compile(r"(\[/)([^\\\]]*?)(\\\])"), "[/", "\\]"),
        (re.compile(r"(\[\\)([^\\\]]*?)(\\\])"), "[\\", "\\]"),
        (re.compile(r"(\[)([^\[\]]*?)(\])"), "[", "]"),
        (re.compile(r"(\{)([^{}]*?)(\})"), "{", "}"),
        (re.compile(r"(\()([^()]*?)(\))"), "(", ")"),
        # Flag: `>label]` — but only when not already part of `-->` arrow.
        # We detect by requiring a word char or `>` right before, but not `-`.
        (re.compile(r"(?<![-=])(>)([^>\]]*?)(\])"), ">", "]"),
    )

    def needs_quote(inner: str) -> bool:
        if not inner:
            return False
        if inner.startswith('"') and inner.endswith('"'):
            return False  # already quoted
        return bool(re.search(r'[<>#|"\'\\]', inner))

    for pat, open_, close_ in patterns:
        def _sub(m: re.Match[str]) -> str:
            nonlocal changed_any
            inner = m.group(2)
            if needs_quote(inner):
                # Strip the char set that even quoted labels dislike.
                cleaned = re.sub(r'["`]', "", inner)
                changed_any = True
                return f'{m.group(1)}"{cleaned}"{m.group(3)}'
            return m.group(0)

        text = pat.sub(_sub, text)

    return (text, "quote_dangerous_labels" if changed_any else None)


def _step_close_brackets(text: str) -> tuple[str, Optional[str]]:
    """Step H — if a line opens more `[` than it closes, append closing
    brackets. Works around LLM's tendency to drop the final `]`."""
    lines = text.split("\n")
    changed = False
    out: list[str] = []
    for line in lines:
        opens = line.count("[")
        closes = line.count("]")
        if opens > closes:
            line = line + ("]" * (opens - closes))
            changed = True
        out.append(line)
    return ("\n".join(out), "close_brackets" if changed else None)


def _step_fix_quoted_edge_labels(text: str) -> tuple[str, Optional[str]]:
    """Step I-pre — Mermaid edge labels live INSIDE pipes (`-->|label|`),
    not inside quotes between dashes (`-- "label" -->`).  The LLM
    routinely emits the wrong shape across EVERY arrow style:

        BUCK -- "+5 V"   --> LDO1          (normal)
        A    == "thick"  ==> B              (thick)
        CLK1 -. "170 MHz".-> ADC1          (dotted)
        A    ~~ "invis"  ~~> B              (invisible)

    All four are parse errors. Convert each to the canonical pipe form:

        BUCK -->|+5 V| LDO1
        A    ==>|thick| B
        CLK1 -.->|170 MHz| ADC1
        A    ~~~|invis| B

    Regression for the 2026-04-24 power-tree + channelised-FE diagrams
    (the dotted-arrow form was the breaking case on the FE clock distribution).
    """
    original = text
    # Three arrow styles with three tail tokens:
    #   normal:  left `--`,  tail `-->`
    #   thick:   left `==`,  tail `==>`
    #   dotted:  left `-.`,  tail `.->`    (leading `.` is the anchor)
    # Previous version had tail `-.->` which is the UNLABELED dotted form
    # — when a label is between `-.` and `.->`, the tail is just `.->`.
    # That's why screenshot 2026-04-24 dotted edges still broke the parser.
    pattern = re.compile(
        r"(\b[\w][\w-]*\b)\s*"              # 1: source node
        r"(==|--|-\.)"                      # 2: arrow style (thick/normal/dotted)
        r"\s*\"([^\"]+)\"\s*"               # 3: quoted label
        r"(==>|-->|\.->)"                   # 4: arrow tail
        r"\s*(\b[\w][\w-]*\b)"              # 5: dest node
    )

    def _sub(m: re.Match[str]) -> str:
        src, style, label, tail, dst = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        # Pick the dominant arrow form:
        #   thick (`==`) > dotted (`-.`) > normal (`--`)
        if "==" in style or "==" in tail:
            arrow = "==>"
        elif "-." in style or tail.startswith("."):
            arrow = "-.->"
        else:
            arrow = "-->"
        # Strip leading/trailing whitespace + any stray quotes from the label.
        clean_label = label.strip().strip("'\"`")
        return f"{src} {arrow}|{clean_label}| {dst}"

    text = pattern.sub(_sub, text)
    if text != original:
        return text, "fix_quoted_edge_labels"
    return text, None


def _step_isolate_end(text: str) -> tuple[str, Optional[str]]:
    """Step I — if `end` appears on the same line as other content (e.g.
    `FOO[X] end`), split it so `end` is alone. Mermaid requires `end` to
    close a subgraph on its own line."""
    lines = text.split("\n")
    out: list[str] = []
    changed = False
    for line in lines:
        # Match `... end` at end of line, where `... ` has content.
        m = re.match(r"^(\s*)(.+?)\s+end\s*$", line)
        if m and m.group(2).strip() and not m.group(2).rstrip().endswith("end"):
            out.append(f"{m.group(1)}{m.group(2)}")
            out.append(f"{m.group(1)}end")
            changed = True
        else:
            out.append(line)
    return ("\n".join(out), "isolate_end" if changed else None)


def _step_trim(text: str) -> tuple[str, Optional[str]]:
    """Step final — strip trailing whitespace on each line + collapse 3+
    blank lines to 1. Keeps output tidy."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append(ln)
        else:
            blank = 0
            out.append(ln)
    return ("\n".join(out).strip() + "\n", None)  # never report


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

_STEPS = (
    _step_asciify,
    _step_normalise_arrows,
    _step_strip_frontmatter,
    _step_strip_direction,
    _step_normalise_header,
    _step_fix_bare_shapes,
    _step_quote_dangerous_labels,
    _step_close_brackets,
    _step_fix_quoted_edge_labels,
    _step_isolate_end,
    _step_trim,
)


def salvage(raw: str) -> tuple[str, list[str]]:
    """Best-effort fix for raw LLM-emitted Mermaid. Returns
    (cleaned_text, list_of_fixes_applied).

    The list is for observability only — ops can grep it to see which
    classes of LLM mis-emission are most common and adjust prompts.

    `cleaned_text` is always a string ending in a newline; if everything
    fails, returns FALLBACK_DIAGRAM unchanged and reports `"fallback"`.
    """
    if not raw or not isinstance(raw, str):
        return FALLBACK_DIAGRAM, ["fallback"]

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    fixes: list[str] = []
    for step in _STEPS:
        text, fix = step(text)
        if fix:
            fixes.append(fix)

    # Last-chance sanity gate — if the result doesn't start with a known
    # diagram type keyword, we lost the plot; return the fallback.
    first_line = next(
        (ln.strip() for ln in text.split("\n") if ln.strip()),
        "",
    ).lower()
    if not any(first_line.startswith(t) for t in _DIAGRAM_TYPES):
        return FALLBACK_DIAGRAM, fixes + ["fallback"]

    return text, fixes
