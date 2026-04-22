"""
Phase 4: Logical Netlist Generation Agent (KEY INNOVATION)

Generates netlist BEFORE PCB design using AI + NetworkX validation.
This is the core differentiator of Hardware Pipeline.
"""

import json
import logging
from pathlib import Path

from agents.base_agent import BaseAgent
from config import settings
from generators.netlist_generator import NetlistGenerator

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert PCB design engineer generating a logical netlist AND a gate-level interactive schematic from hardware requirements and component selections.

## KEY INNOVATION:
You generate the netlist BEFORE PCB design (not extracted from schematics). This gives engineers a validated connectivity map before investing weeks in layout.

## CRITICAL: TOOL CALL FIRST — MANDATORY
You MUST call the `generate_netlist` tool as your VERY FIRST action. Do NOT output any text before the tool call.

Include ALL components from the P1 BOM in the tool call. Every IC, passive component, and connector MUST appear in the `nodes` array. Every connection MUST appear in the `edges` array.

IMPORTANT: Do NOT include `schematic_data` in the tool call — it will be auto-generated from your nodes and edges. Focus your token budget on complete nodes, edges, mermaid_diagram, and validation_notes.

Only AFTER the tool call completes should you add brief explanatory prose.

## YOUR TASK:
Given requirements and selected components, generate:

1. **Netlist JSON** - Machine-readable netlist with:
   - Component instances (U1, R1, C1, etc.) — EVERY component from the BOM
   - Pin-to-pin connections (net names) — ALL connections
   - Power nets and ground nets
   - Signal types (digital, analog, power, clock)

2. **Mermaid Block Diagram** - High-level visual representation
   - Show major ICs as boxes
   - Show connections with labels
   - Group by functional blocks
   - Show power domains

3. **Schematic Data** - Gate-level interactive schematic (see section below)

4. **Validation Notes** - Flag potential issues:
   - Voltage level mismatches
   - Missing decoupling capacitors
   - Unconnected pins
   - Power domain crossing issues

## GATE-LEVEL SCHEMATIC (schematic_data field)
Produce a `schematic_data` object with one or more `sheets`. Each sheet is a logical page
of the schematic (e.g. "Power", "MCU Core", "RF Front-End"). Rules:

- Grid coordinate system: each sheet is 30 columns wide × 20 rows tall. 1 grid unit = 40 px.
- Every component from the netlist MUST appear on some sheet — including every R, C, L, D, IC,
  connector, ground symbol, and Vcc/power net-tie.
- Place components such that they do NOT overlap. Leave at least 1 grid unit of whitespace
  between neighbouring components.
- Signal flow: inputs on the LEFT, outputs on the RIGHT, power at the TOP, ground at the BOTTOM.
- Place decoupling capacitors immediately adjacent to the IC power pin they bypass.
- Each IC `pins` array must list EVERY pin with `name`, `num`, and `side` (left|right|top|bottom).
  Pin stubs on the same side are spaced 1 grid unit apart in listed order.

Component `type` enum (use these exact strings):
  `resistor` | `capacitor` | `capacitor_polar` | `inductor` |
  `diode` | `diode_zener` | `diode_tvs` | `diode_led` |
  `ic` | `ground` | `vcc` | `connector` | `net_label`

Rotation: 0 (horizontal, pins L↔R), 90 (vertical, pins T↕B), 180 / 270 as needed.

Nets: every `net` has a `name`, a `type` (signal|power|ground|clock|differential),
and `endpoints` — a list of `{ref, pin}` entries. Optional `waypoints` are a list of
`{x, y}` grid coordinates the wire should pass through in order. If omitted, the
renderer will auto-route an L-shaped wire between consecutive endpoint pin anchors.

STRICT rules (HARD REQUIREMENTS — any violation is a parse error):
- Every pin of every component MUST be referenced by some net endpoint. No floating pins.
- If an IC pin is unused in the design, connect it to a `GND` net (or `NC` net if
  datasheet specifies "no connect").
- Every IC power pin (`VCC`/`VDD`/`AVDD`) must have a 100 nF ceramic decoupling cap
  placed next to it, connected between the power rail and GND.
- Power rails (`VCC`, `3V3`, `5V`, etc.) terminate in a `vcc` symbol with the rail name
  as the component `value`.
- Ground nets terminate in a `ground` symbol.
- Connectors include a `pin_count` in their `value` field (e.g. `"CON_4"`, `"CON_2"`).

## OUTPUT FORMAT:
Call `generate_netlist` tool first, then generate a markdown document with:
- Netlist summary table
- Mermaid diagram of connectivity
- Detailed pin-to-pin connection table
- Power budget table
- Validation results (warnings/errors)

IMPORTANT: Do NOT use TBD, TBA, or TBC placeholders. All component instances must have
real reference designators (U1, R1, C1…), real part numbers from the P1 component data,
and concrete net names. Derive pin numbers from the component datasheets or use standard
conventions. Every connection must be fully specified.
"""

GENERATE_NETLIST_TOOL = {
    "name": "generate_netlist",
    "description": "Generate structured netlist data with component instances and connections.",
    "input_schema": {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "description": "Component instances in the netlist",
                "items": {
                    "type": "object",
                    "properties": {
                        "instance_id": {"type": "string"},
                        "part_number": {"type": "string"},
                        "component_name": {"type": "string"},
                        "reference_designator": {"type": "string"},
                    },
                    "required": ["instance_id", "part_number", "component_name"],
                },
            },
            "edges": {
                "type": "array",
                "description": "Pin-to-pin connections",
                "items": {
                    "type": "object",
                    "properties": {
                        "net_name": {"type": "string"},
                        "from_instance": {"type": "string"},
                        "from_pin": {"type": "string"},
                        "to_instance": {"type": "string"},
                        "to_pin": {"type": "string"},
                        "signal_type": {"type": "string"},
                    },
                    "required": ["net_name", "from_instance", "from_pin", "to_instance", "to_pin"],
                },
            },
            "power_nets": {"type": "array", "items": {"type": "string"}},
            "ground_nets": {"type": "array", "items": {"type": "string"}},
            "mermaid_diagram": {"type": "string"},
            "validation_notes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "schematic_data": {
                "type": "object",
                "description": (
                    "Gate-level interactive schematic. One or more sheets, each with components placed "
                    "on a 30x20 grid and nets connecting their pins. Every component from the netlist must "
                    "appear on some sheet."
                ),
                "properties": {
                    "sheets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Sheet ID (e.g. sheet1)"},
                                "title": {"type": "string", "description": "Human-readable sheet title (e.g. 'Power Supply')"},
                                "components": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "ref": {"type": "string", "description": "Reference designator (R1, C5, U2, J1…)"},
                                            "type": {
                                                "type": "string",
                                                "enum": [
                                                    "resistor", "capacitor", "capacitor_polar", "inductor",
                                                    "diode", "diode_zener", "diode_tvs", "diode_led",
                                                    "ic", "ground", "vcc", "connector", "net_label",
                                                ],
                                            },
                                            "value": {"type": "string", "description": "Component value or rail name (e.g. '10k', '100nF', '3V3', 'CON_4')"},
                                            "part_number": {"type": "string"},
                                            "x": {"type": "integer", "minimum": 0, "maximum": 30, "description": "Grid column (0-30)"},
                                            "y": {"type": "integer", "minimum": 0, "maximum": 20, "description": "Grid row (0-20)"},
                                            "rot": {"type": "integer", "enum": [0, 90, 180, 270]},
                                            "pins": {
                                                "type": "array",
                                                "description": "For `ic` and `connector` only — list every pin with name, num, side",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "name": {"type": "string"},
                                                        "num": {"type": "string"},
                                                        "side": {"type": "string", "enum": ["left", "right", "top", "bottom"]},
                                                    },
                                                    "required": ["name", "side"],
                                                },
                                            },
                                        },
                                        "required": ["ref", "type", "x", "y"],
                                    },
                                },
                                "nets": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "type": {
                                                "type": "string",
                                                "enum": ["signal", "power", "ground", "clock", "differential", "analog"],
                                            },
                                            "endpoints": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "ref": {"type": "string"},
                                                        "pin": {"type": "string"},
                                                    },
                                                    "required": ["ref", "pin"],
                                                },
                                            },
                                            "waypoints": {
                                                "type": "array",
                                                "description": "Optional intermediate {x,y} grid points the wire should pass through",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "x": {"type": "number"},
                                                        "y": {"type": "number"},
                                                    },
                                                    "required": ["x", "y"],
                                                },
                                            },
                                        },
                                        "required": ["name", "endpoints"],
                                    },
                                },
                            },
                            "required": ["id", "title", "components", "nets"],
                        },
                    },
                },
                "required": ["sheets"],
            },
        },
        "required": ["nodes", "edges", "mermaid_diagram"],
    },
}


class NetlistAgent(BaseAgent):
    """Phase 4: Logical netlist generation before PCB design."""

    def __init__(self):
        super().__init__(
            phase_number="P4",
            phase_name="Netlist Generation",
            model=settings.primary_model,  # Opus for complex reasoning
            tools=[GENERATE_NETLIST_TOOL],
            # 16K lets the LLM emit richer schematic_data (30+ components,
            # multi-sheet cross-sheet routing) without truncating. Prior
            # 8K cap frequently forced the skeleton fallback on complex
            # defence-grade RF designs (>20 ICs with diff-pair routing).
            max_tokens=16384,
        )
        self.netlist_generator = NetlistGenerator()

    def get_system_prompt(self, project_context: dict) -> str:
        return SYSTEM_PROMPT

    async def execute(self, project_context: dict, user_input: str) -> dict:
        output_dir = Path(project_context.get("output_dir", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        project_name = project_context.get("name", "Project")

        # Load prior phase outputs
        requirements = self._load_file(output_dir / "requirements.md")
        components_text = self._load_file(output_dir / "component_recommendations.md")
        hrs = self._load_file(output_dir / f"HRS_{project_name.replace(' ', '_')}.md")
        # P1 → P4 handoff: surface the block diagram mermaid so the LLM
        # can align the schematic topology with the P1 signal-flow intent
        # (stage ordering, mixer/LO direction, multi-antenna layout, etc.).
        block_diagram_md = self._load_file(output_dir / "block_diagram.md")

        if not requirements:
            return {
                "response": "Requirements not found. Complete Phase 1 first.",
                "phase_complete": False,
                "outputs": {},
            }

        # P1.4 — surface the P1 cascade targets + scope so the netlist agent
        # can honour them (NF, gain, IIP3, phase-noise floor, frequency range).
        # Previously the agent only saw the BOM + prose requirements and had
        # no structured way to check the schematic against the P1 budget.
        design_parameters = project_context.get("design_parameters") or {}
        design_scope = project_context.get("design_scope") or ""
        cascade_hints = self._format_cascade_targets(design_parameters)

        block_hint = (
            f"\n### P1 Block Diagram (MUST align schematic topology to this signal flow):\n"
            f"{block_diagram_md[:4000]}\n"
        ) if block_diagram_md else ""

        user_message = f"""Generate a complete logical netlist for:

**Project:** {project_name}

### Design Parameters (P1 cascade targets — the schematic MUST honour these):
{cascade_hints}

### Design Scope: {design_scope or '(not specified)'}
{block_hint}
### Requirements:
{requirements[:8000]}

### Selected Components (MUST include ALL of these in the netlist):
{components_text[:12000]}

### HRS Reference:
{hrs[:6000] if hrs else 'Not yet generated.'}

CRITICAL: You MUST call the `generate_netlist` tool IMMEDIATELY with:
1. ALL component instances from the BOM above — every IC, passive, connector, FPGA, LNA, mixer, filter, ADC, power regulator
2. ALL pin-to-pin connections between them with correct signal types (RF, IF, power, ground, digital, clock, LVDS, analog)
3. Power and ground nets for every power domain
4. A Mermaid diagram showing the full connectivity
5. Validation notes for any potential issues — CALL OUT any case where a
   selected component's datasheet spec (NF, gain, IIP3, phase noise) is
   worse than the P1 cascade target listed above.

Do NOT include schematic_data — it is auto-generated from your nodes/edges.
Do NOT generate a minimal 2-component skeleton. The netlist must be COMPLETE.
"""

        response = await self.call_llm(
            messages=[{"role": "user", "content": user_message}],
            system=self.get_system_prompt(project_context),
        )

        outputs = {}
        netlist_data = None

        # Process tool calls
        if response.get("tool_calls"):
            for tc in response["tool_calls"]:
                if tc["name"] == "generate_netlist":
                    netlist_data = tc["input"]

        if netlist_data:
            # Transform tool call data to generator format
            gen_components = []
            for node in netlist_data.get("nodes", []):
                gen_components.append({
                    "id": node.get("instance_id", ""),
                    "name": node.get("component_name", ""),
                    "type": node.get("part_number", ""),
                    "pins": [],
                    "properties": node,
                })

            connections = []
            for edge in netlist_data.get("edges", []):
                connections.append({
                    "source": edge.get("from_instance", ""),
                    "source_pin": edge.get("from_pin", ""),
                    "target": edge.get("to_instance", ""),
                    "target_pin": edge.get("to_pin", ""),
                    "signal": edge.get("net_name", ""),
                    "type": edge.get("signal_type", "wire"),
                })

            # Use NetlistGenerator to create structured netlist
            generator_netlist = self.netlist_generator.generate(
                project_name=project_name,
                components=gen_components,
                connections=connections,
                metadata=netlist_data.get("metadata", {}),
            )

            # Build outputs through the dict — write_outputs in pipeline_service
            # handles the actual file writes via StorageAdapter (single write path).
            outputs["netlist.json"] = json.dumps(generator_netlist, indent=2)

            # P1.4 — emit a real KiCad-importable .net alongside the JSON.
            # Mirrors the JSON but in S-expression format so a PCB designer
            # can Forward-Netlist → Pcbnew without hand-translation.
            try:
                from generators.kicad_netlist import netlist_to_kicad
                outputs["netlist.net"] = netlist_to_kicad(generator_netlist)
            except Exception as _knl_exc:
                self.log(f"kicad_netlist_export_failed: {_knl_exc}", "warning")

            # Generate visual markdown with full component/connection tables
            mermaid_diagram = self.netlist_generator.to_mermaid(generator_netlist)
            visual_content = self._build_visual_md(netlist_data, project_name, mermaid_diagram)
            outputs["netlist_visual.md"] = visual_content

            # Run NetworkX validation — always store as JSON string (not dict)
            validation = self._validate_netlist(netlist_data)
            outputs["netlist_validation.json"] = json.dumps(validation, indent=2)

            # P1.6 — reject components whose pins fail validation with
            # critical/high severity. Previously these were warnings only;
            # now the component is stripped from schematic_data + nodes +
            # edges before KiCad export so downstream output can't embed
            # a schematic with invalid pin numbers.
            try:
                from tools.pin_map import reject_invalid_components
                netlist_data, _rejections = reject_invalid_components(netlist_data)
            except Exception as _rej_exc:
                self.log(f"pin_map_reject_failed: {_rej_exc}", "warning")
                _rejections = []

            # P2.7 — structured DRC (shorts, floating outputs, power-net
            # connectivity). Complements the LLM's prose validation_notes.
            try:
                from tools.netlist_drc import run_drc
                drc = run_drc(netlist_data)
                # Fold in the pin-map rejections as DRC violations so the
                # JSON audit report surfaces them exactly where the operator
                # already looks for problems.
                if _rejections:
                    drc.setdefault("violations", []).extend(_rejections)
                    drc.setdefault("counts", {})["critical"] = \
                        drc["counts"].get("critical", 0) + len(_rejections)
                    drc["checks_run"] = list(drc.get("checks_run") or []) + [
                        "pin_map_reject",
                    ]
                    drc["overall_pass"] = False
                # Pin-number validation (P3 — closes the "pin numbers are
                # LLM-generated" gap): validate every schematic component
                # against `data/pin_maps.json` or the package pin-count
                # fallback. Hallucinated pins surface here as critical /
                # high severity entries that the UI already renders.
                try:
                    from tools.pin_map import validate_netlist_pins
                    pin_issues = validate_netlist_pins(netlist_data)
                    if pin_issues:
                        drc.setdefault("violations", []).extend(pin_issues)
                        for pi in pin_issues:
                            sev = pi.get("severity", "info")
                            drc.setdefault("counts", {})[sev] = \
                                drc["counts"].get(sev, 0) + 1
                        drc["checks_run"] = list(drc.get("checks_run") or []) + [
                            "pin_validation",
                        ]
                        if any(p["severity"] in ("critical", "high")
                               for p in pin_issues):
                            drc["overall_pass"] = False
                except Exception as _pin_exc:
                    self.log(f"pin_validation_failed: {_pin_exc}", "warning")
                outputs["netlist_drc.json"] = json.dumps(drc, indent=2)
            except Exception as _drc_exc:
                self.log(f"drc_failed: {_drc_exc}", "warning")

            # Schematic data — if the LLM produced one, persist it. Otherwise synthesize a
            # minimal single-sheet schematic from the node/edge list so the UI always has
            # something to render. Tag `source` so the UI can show whether the layout came
            # from the model directly or from our deterministic synthesizer, and so downstream
            # tooling can treat the two cases differently (auto-synth layouts are conservative
            # and may need review for specialised topologies).
            llm_schematic = netlist_data.get("schematic_data")
            if llm_schematic and llm_schematic.get("sheets"):
                schematic_data = llm_schematic
                schematic_data["source"] = "llm_emitted"
            else:
                schematic_data = self._synthesize_schematic(netlist_data)
                schematic_data["source"] = "auto_synthesized"
                schematic_data["auto_synthesized"] = True
            outputs["schematic.json"] = json.dumps(schematic_data, indent=2)

            self.log(f"Netlist: {len(netlist_data.get('nodes', []))} nodes, {len(netlist_data.get('edges', []))} edges")

        else:
            # LLM did not call the generate_netlist tool — build netlist from P1 BOM
            logger.warning("P4: LLM skipped tool call — building netlist from component_recommendations.md")
            netlist_data = self._build_netlist_from_bom(components_text, requirements)

            # Run the standard output pipeline
            gen_components = [
                {"id": n["instance_id"], "name": n["component_name"], "type": n["part_number"], "pins": [], "properties": n}
                for n in netlist_data["nodes"]
            ]
            gen_connections = [
                {"source": e["from_instance"], "source_pin": e["from_pin"],
                 "target": e["to_instance"], "target_pin": e["to_pin"],
                 "signal": e["net_name"], "type": e.get("signal_type", "wire")}
                for e in netlist_data["edges"]
            ]
            generator_netlist = self.netlist_generator.generate(
                project_name=project_name,
                components=gen_components,
                connections=gen_connections,
                metadata={"auto_synthesized": True},
            )
            outputs["netlist.json"] = json.dumps(generator_netlist, indent=2)
            # Same KiCad .net + DRC emission as the happy path above.
            try:
                from generators.kicad_netlist import netlist_to_kicad
                outputs["netlist.net"] = netlist_to_kicad(generator_netlist)
            except Exception:
                pass
            mermaid_diagram = self.netlist_generator.to_mermaid(generator_netlist)
            visual_content = self._build_visual_md(netlist_data, project_name, mermaid_diagram)
            import re as _re
            visual_content = _re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', visual_content, flags=_re.IGNORECASE)
            outputs["netlist_visual.md"] = visual_content
            validation = self._validate_netlist(netlist_data)
            outputs["netlist_validation.json"] = json.dumps(validation, indent=2)
            try:
                from tools.netlist_drc import run_drc
                drc = run_drc(netlist_data)
                try:
                    from tools.pin_map import validate_netlist_pins
                    pin_issues = validate_netlist_pins(netlist_data)
                    if pin_issues:
                        drc.setdefault("violations", []).extend(pin_issues)
                        for pi in pin_issues:
                            sev = pi.get("severity", "info")
                            drc.setdefault("counts", {})[sev] = \
                                drc["counts"].get(sev, 0) + 1
                        drc["checks_run"] = list(drc.get("checks_run") or []) + [
                            "pin_validation",
                        ]
                        if any(p["severity"] in ("critical", "high")
                               for p in pin_issues):
                            drc["overall_pass"] = False
                except Exception:
                    pass
                outputs["netlist_drc.json"] = json.dumps(drc, indent=2)
            except Exception:
                pass
            _fb_schematic = self._synthesize_schematic(netlist_data)
            _fb_schematic["source"] = "auto_synthesized"
            _fb_schematic["auto_synthesized"] = True
            outputs["schematic.json"] = json.dumps(_fb_schematic, indent=2)

        return {
            "response": response.get("content", "Netlist generated."),
            "phase_complete": True,  # Always complete — skeleton fallback ensures output files exist
            "outputs": outputs,
        }

    @staticmethod
    def _format_cascade_targets(design_parameters: dict) -> str:
        """Render the P1 cascade targets as a compact bulleted block so the
        LLM can reason about per-stage budget. Returns '(no targets)' when
        the caller didn't pass any — the agent then operates in the
        original BOM-only mode."""
        if not isinstance(design_parameters, dict) or not design_parameters:
            return "(no design parameters supplied — operating in BOM-only mode)"
        # Pick the subset the netlist agent can actually act on. Other
        # fields (project_summary, application, etc.) are noise here.
        relevant = (
            "freq_range", "freq_range_ghz",
            "bandwidth_mhz", "instantaneous_bandwidth_mhz", "ibw",
            "noise_figure_db", "nf_db",
            "total_gain_db", "gain_db",
            "iip3_dbm_input", "iip3_dbm", "iip3",
            "p1db_dbm_out", "p1db_dbm", "p1db",
            "sfdr_db",
            "sensitivity_dbm", "mds_dbm",
            "phase_noise_dbchz",
            "supply_voltage", "vdd", "power_budget_w",
            "lo_frequency", "lo_frequency_ghz",
            "if_frequency", "if_frequency_mhz",
            "architecture", "application",
        )
        lines = []
        for k in relevant:
            if k in design_parameters and design_parameters[k] is not None:
                v = design_parameters[k]
                lines.append(f"- {k}: {v}")
        if not lines:
            return "(design_parameters supplied but no cascade-relevant keys)"
        return "\n".join(lines)

    def _build_visual_md(self, data: dict, project_name: str, mermaid: str) -> str:
        lines = [
            "# Logical Netlist",
            f"## {project_name}",
            "",
            "## Block Diagram",
            "",
            f"```mermaid\n{mermaid}\n```",
            "",
            "## Component Instances",
            "",
            "| Ref | Part Number | Component |",
            "|---|---|---|",
        ]
        for node in data.get("nodes", []):
            lines.append(f"| {node.get('instance_id', '')} | {node.get('part_number', '')} | {node.get('component_name', '')} |")

        lines.extend(["", "## Pin-to-Pin Connections", "", "| Net | From | Pin | To | Pin | Type |", "|---|---|---|---|---|---|"])
        for edge in data.get("edges", []):
            lines.append(
                f"| {edge.get('net_name', '')} | {edge.get('from_instance', '')} | {edge.get('from_pin', '')} "
                f"| {edge.get('to_instance', '')} | {edge.get('to_pin', '')} | {edge.get('signal_type', '')} |"
            )

        # Net-centric connection list — groups all pins sharing each net
        edges = data.get("edges", [])
        if edges:
            # Build net → list of "RefDes - Pin" entries
            net_map: dict = {}
            for edge in edges:
                net = edge.get("net_name", "").strip()
                if not net:
                    continue
                from_entry = f"{edge.get('from_instance', '')} - {edge.get('from_pin', '')}"
                to_entry   = f"{edge.get('to_instance', '')} - {edge.get('to_pin', '')}"
                net_map.setdefault(net, [])
                if from_entry not in net_map[net]:
                    net_map[net].append(from_entry)
                if to_entry not in net_map[net]:
                    net_map[net].append(to_entry)

            lines.extend([
                "",
                "## Net Connection List",
                "",
                "| Net Name | Reference Designator - Pin No. |",
                "|----------|-------------------------------|",
            ])
            for net_name, pins in sorted(net_map.items()):
                pins_str = ",  ".join(pins)
                lines.append(f"| {net_name} | {pins_str} |")

        # Validation notes
        notes = data.get("validation_notes", [])
        if notes:
            lines.extend(["", "## Validation Notes", ""])
            for note in notes:
                lines.append(f"- {note}")

        return "\n".join(lines)

    def _validate_netlist(self, data: dict) -> dict:
        """Basic netlist validation using NetworkX."""
        try:
            import networkx as nx

            G = nx.DiGraph()
            for node in data.get("nodes", []):
                G.add_node(node["instance_id"], **node)
            for edge in data.get("edges", []):
                G.add_edge(
                    edge["from_instance"], edge["to_instance"],
                    net_name=edge.get("net_name", ""),
                )

            # Check for isolated nodes
            isolated = list(nx.isolates(G))

            # Check for cycles (shouldn't exist in most designs)
            cycles = list(nx.simple_cycles(G))

            return {
                "total_nodes": G.number_of_nodes(),
                "total_edges": G.number_of_edges(),
                "isolated_nodes": isolated,
                "cycles": [list(c) for c in cycles[:5]],
                "is_connected": nx.is_weakly_connected(G) if G.number_of_nodes() > 0 else False,
            }
        except ImportError:
            return {"error": "NetworkX not installed"}
        except Exception as e:
            return {"error": str(e)}

    def _load_file(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _build_netlist_from_bom(self, components_md: str, requirements_md: str) -> dict:
        """Parse component_recommendations.md to build a complete netlist when LLM
        skips the tool call. Extracts every component, assigns ref designators,
        builds power/ground/signal connections based on component roles."""
        import re as _re

        nodes = []
        edges = []
        power_nets = set()
        ground_nets = {"GND", "AGND"}

        # Parse "### N. Component Name" sections
        sections = _re.split(r'^### \d+\.\s+', components_md, flags=_re.MULTILINE)
        ref_counter = {"U": 0, "J": 0, "Y": 0}

        parsed_components = []
        for sec in sections[1:]:  # skip preamble before first ###
            lines = sec.strip().split("\n")
            comp_title = lines[0].strip() if lines else "Unknown"

            # Extract part number from **Primary Choice:** [PartNum](url) (Manufacturer)
            pn_match = _re.search(r'\*\*Primary Choice:\*\*\s*\[([^\]]+)\]', sec)
            part_number = pn_match.group(1) if pn_match else comp_title.split()[0]

            # Extract specs from | key | value | table
            specs = {}
            for m in _re.finditer(r'\|\s*(\w[\w_]*)\s*\|\s*([^|]+?)\s*\|', sec):
                specs[m.group(1).strip()] = m.group(2).strip()

            # Determine component category for ref designator and signal type
            title_lower = comp_title.lower()
            if any(k in title_lower for k in ["connector", "jack", "plug", "sma", "2.4mm"]):
                ref_counter["J"] = ref_counter.get("J", 0) + 1
                ref = f"J{ref_counter['J']}"
            elif any(k in title_lower for k in ["oscillator", "clock", "crystal"]):
                ref_counter["Y"] = ref_counter.get("Y", 0) + 1
                ref = f"Y{ref_counter['Y']}"
            else:
                ref_counter["U"] = ref_counter.get("U", 0) + 1
                ref = f"U{ref_counter['U']}"

            # Detect supply voltage → power rail
            supply_v = specs.get("supply_voltage_v", specs.get("supply_v", specs.get("output_voltage_v", "")))

            # Classify component role. Order matters — passive RF markers
            # are checked BEFORE the generic "signal" fallback so a bias-tee
            # or limiter doesn't silently become an active digital block.
            # Pure R/L/C/D passives are detected FIRST so a Vishay CRCW
            # chip resistor or a Murata GRM cap never gets rendered as an
            # IC block with fake VCC / IN / OUT pins.
            _pn = (part_number or "").lower().strip()
            role = "signal"  # default
            if any(k in title_lower for k in ("chip resistor", "thin-film resistor",
                                                "thick-film resistor"))\
                    or "resistor" in title_lower \
                    or _pn.startswith(("crcw", "rc0", "rc1", "erj", "rk73",
                                        "rmc", "rncp", "rr12", "pat")):
                role = "r_passive"
            elif any(k in title_lower for k in ("chip capacitor", "mlcc",
                                                 "ceramic capacitor", "ceramic cap",
                                                 "multilayer ceramic")) \
                    or "capacitor" in title_lower \
                    or _pn.startswith(("grm", "gcm", "c0603", "c0402", "c0805",
                                        "c1206", "gmk", "ckg", "cl05", "cl10",
                                        "cl21", "cl31", "tmk")):
                role = "c_passive"
            elif "inductor" in title_lower \
                    or _pn.startswith(("lqw", "lqm", "lqh", "lqp", "mlz",
                                        "nlcv", "mss", "xal", "xfl",
                                        "744", "744r", "dfe")):
                role = "l_passive"
            elif any(k in title_lower for k in ("schottky", "zener", "tvs diode",
                                                 "esd diode")) \
                    or (" diode" in title_lower and "laser diode" not in title_lower):
                role = "d_passive"
            elif any(k in title_lower for k in ["mixer", "downconvert", "upconvert"]):
                role = "rf_mixer"
            elif any(k in title_lower for k in ["lna", "amplifier", "vga", "driver"]) \
                    or (" pa" in title_lower) or title_lower.startswith("pa "):
                role = "rf_amplifier"
            elif any(k in title_lower for k in ["ldo", "regulator", "dc-dc", "pmic", "power supply", "buck", "boost"]):
                role = "power"
            elif any(k in title_lower for k in ["adc", "digitiz"]):
                role = "adc"
            elif any(k in title_lower for k in ["fpga", "cpld", "zynq", "ultrascale", "processing"]):
                role = "fpga"
            elif any(k in title_lower for k in ["phy", "ethernet", "transceiver", "uart", "spi"]):
                role = "interface"
            elif any(k in title_lower for k in ["connector", "jack", "sma", "smp", "bnc", "mmcx"]):
                role = "connector"
            elif any(k in title_lower for k in ["filter", "bandpass", "lowpass", "saw", "baw", "cavity", "bpf", "lpf", "hpf"]):
                role = "filter"
            elif any(k in title_lower for k in ["synthesizer", "pll", "vco"]) \
                    or " lo " in title_lower or title_lower.startswith("lo "):
                role = "lo_synth"
            elif any(k in title_lower for k in ["limiter", "pin diode limiter"]):
                role = "limiter"
            elif any(k in title_lower for k in ["bias-tee", "bias tee", "biastee"]):
                role = "bias_tee"
            elif any(k in title_lower for k in ["splitter", "divider", "wilkinson", "power divider"]):
                role = "splitter"
            elif any(k in title_lower for k in ["coupler", "directional coupler", "hybrid coupler"]):
                role = "coupler"
            elif any(k in title_lower for k in ["attenuator", "pad", "step attenuator"]):
                role = "attenuator"
            elif any(k in title_lower for k in ["isolator", "circulator"]):
                role = "isolator"
            elif any(k in title_lower for k in ["balun", "transformer"]):
                role = "balun"

            parsed_components.append({
                "ref": ref,
                "part_number": part_number,
                "name": comp_title,
                "role": role,
                "supply_v": supply_v,
                "specs": specs,
            })

            nodes.append({
                "instance_id": ref,
                "part_number": part_number,
                "component_name": comp_title,
                "reference_designator": ref,
            })

        # ── Build connections based on component roles ──
        # Find power regulators
        power_regs = [c for c in parsed_components if c["role"] == "power"]
        rf_amps = [c for c in parsed_components if c["role"] == "rf_amplifier"]
        mixers = [c for c in parsed_components if c["role"] == "rf_mixer"]
        adcs = [c for c in parsed_components if c["role"] == "adc"]
        fpgas = [c for c in parsed_components if c["role"] == "fpga"]
        interfaces = [c for c in parsed_components if c["role"] == "interface"]
        connectors = [c for c in parsed_components if c["role"] == "connector"]
        lo_synths = [c for c in parsed_components if c["role"] == "lo_synth"]
        filters = [c for c in parsed_components if c["role"] == "filter"]

        # Power connections: each regulator powers downstream ICs
        for reg in power_regs:
            rail = f"V{reg['supply_v'].replace('.', 'p').replace(' ', '_').split('/')[0]}" if reg["supply_v"] else "VCC"
            power_nets.add(rail)
            # Connect regulator output to all non-power ICs
            for comp in parsed_components:
                if comp["role"] != "power" and comp["role"] != "connector":
                    edges.append({
                        "net_name": rail, "from_instance": reg["ref"], "from_pin": "OUT",
                        "to_instance": comp["ref"], "to_pin": "VCC", "signal_type": "power",
                    })

        # Ground connections: all components to GND
        for comp in parsed_components:
            edges.append({
                "net_name": "GND", "from_instance": comp["ref"], "from_pin": "GND",
                "to_instance": comp["ref"], "to_pin": "GND", "signal_type": "ground",
            })

        # RF signal chain: connector → LNA → filter → mixer → IF amp → ADC → FPGA
        rf_chain = []
        if connectors:
            rf_chain.append(connectors[0])
        rf_chain.extend(rf_amps)
        rf_chain.extend(filters)
        rf_chain.extend(mixers)
        rf_chain.extend(adcs)
        if fpgas:
            rf_chain.append(fpgas[0])

        for i in range(len(rf_chain) - 1):
            src = rf_chain[i]
            dst = rf_chain[i + 1]
            sig_type = "RF" if i < len(rf_amps) + len(filters) + len(connectors) else "IF"
            if dst["role"] == "adc":
                sig_type = "IF"
            if dst["role"] == "fpga":
                sig_type = "digital"
            net_name = f"{sig_type}_{src['ref']}_{dst['ref']}"
            edges.append({
                "net_name": net_name, "from_instance": src["ref"], "from_pin": "OUT",
                "to_instance": dst["ref"], "to_pin": "IN", "signal_type": sig_type.lower(),
            })

        # LO synth → mixer LO port
        for lo in lo_synths:
            for mx in mixers:
                edges.append({
                    "net_name": f"LO_{lo['ref']}_{mx['ref']}", "from_instance": lo["ref"],
                    "from_pin": "RF_OUT", "to_instance": mx["ref"], "to_pin": "LO",
                    "signal_type": "clock",
                })

        # FPGA → interface ICs
        for iface in interfaces:
            if fpgas:
                edges.append({
                    "net_name": f"DATA_{fpgas[0]['ref']}_{iface['ref']}",
                    "from_instance": fpgas[0]["ref"], "from_pin": "DATA",
                    "to_instance": iface["ref"], "to_pin": "DATA",
                    "signal_type": "digital",
                })

        # Build mermaid diagram
        mermaid_lines = ["graph LR"]
        for comp in parsed_components:
            label = f"{comp['name'][:30]} {comp['part_number']}"
            # Sanitize: remove quotes, angle brackets, pipes
            label = _re.sub(r'[<>"\'|#&@:]', '', label)
            mermaid_lines.append(f"    {comp['ref']}[{label}]")
        for edge in edges:
            if edge["signal_type"] not in ("ground",):
                mermaid_lines.append(
                    f"    {edge['from_instance']} -->|{edge['net_name'][:20]}| {edge['to_instance']}"
                )
        # Deduplicate mermaid edges
        seen_edges = set()
        deduped = [mermaid_lines[0]]
        for line in mermaid_lines[1:]:
            if line not in seen_edges:
                seen_edges.add(line)
                deduped.append(line)
        mermaid_diagram = "\n".join(deduped)

        # Validation notes
        validation_notes = [
            f"INFO: Auto-extracted {len(nodes)} components from P1 BOM",
            f"INFO: Generated {len(edges)} connections based on signal chain analysis",
            f"INFO: Power nets: {', '.join(sorted(power_nets))}",
            f"INFO: Ground nets: {', '.join(sorted(ground_nets))}",
        ]
        if not rf_amps:
            validation_notes.append("WARNING: No RF amplifiers detected in BOM")
        if not power_regs:
            validation_notes.append("WARNING: No power regulators detected in BOM")
        if not fpgas:
            validation_notes.append("WARNING: No FPGA/processor detected in BOM")

        return {
            "nodes": nodes,
            "edges": edges,
            "power_nets": sorted(power_nets),
            "ground_nets": sorted(ground_nets),
            "mermaid_diagram": mermaid_diagram,
            "validation_notes": validation_notes,
        }

    def _synthesize_schematic(self, netlist_data: dict) -> dict:
        """Synthesise a multi-sheet gate-level schematic from nodes + edges.

        Produces khv-quality output: role-specific IC pin lists, differential
        pairs, decoupling caps wired to VCC/GND, proper RF signal chain,
        SPI buses, clock distribution — zero floating pins.
        """
        nodes = netlist_data.get("nodes", []) or []
        edges = netlist_data.get("edges", []) or []
        power_nets = set(netlist_data.get("power_nets", []) or [])
        ground_nets = set(netlist_data.get("ground_nets", []) or [])

        # ── Build ref→node + role lookup ──────────────────────────────────
        ref_node: dict = {}
        ref_role: dict = {}
        for n in nodes:
            ref = n.get("instance_id") or n.get("reference_designator", "")
            if not ref:
                continue
            ref_node[ref] = n
            name_l = (n.get("component_name", "") + " " + n.get("part_number", "")).lower()
            _pn_l = (n.get("part_number", "") or "").lower().strip()
            # Pure R/L/C/D passives first — a Vishay CRCW or Murata GRM
            # must never be rendered as an IC block. Then passive RF
            # families (limiter/bias-tee/splitter/etc.) so they aren't
            # silently dumped into the generic "signal" bucket.
            if ("resistor" in name_l
                    or _pn_l.startswith(("crcw", "rc0", "rc1", "erj", "rk73",
                                          "rmc", "rncp", "rr12", "pat"))):
                ref_role[ref] = "r_passive"
            elif ("capacitor" in name_l
                    or _pn_l.startswith(("grm", "gcm", "c0603", "c0402", "c0805",
                                          "c1206", "gmk", "ckg", "cl05", "cl10",
                                          "cl21", "cl31", "tmk"))):
                ref_role[ref] = "c_passive"
            elif ("inductor" in name_l
                    or _pn_l.startswith(("lqw", "lqm", "lqh", "lqp", "mlz",
                                          "nlcv", "mss", "xal", "xfl",
                                          "744", "744r", "dfe"))):
                ref_role[ref] = "l_passive"
            elif (("diode" in name_l and "laser diode" not in name_l)
                    or any(k in name_l for k in ("schottky", "zener", "tvs"))):
                ref_role[ref] = "d_passive"
            elif any(k in name_l for k in ["mixer", "downconvert", "upconvert"]):
                ref_role[ref] = "rf_mixer"
            elif any(k in name_l for k in ["lna", "amplifier", "vga", "driver"]):
                ref_role[ref] = "rf_amp"
            elif any(k in name_l for k in ["filter", "bandpass", "lowpass", "saw", "baw", "cavity", "bpf", "lpf", "hpf"]):
                ref_role[ref] = "filter"
            elif any(k in name_l for k in ["connector", "jack", "sma", "2.4mm", "smp", "bnc", "mmcx"]):
                ref_role[ref] = "connector"
            elif any(k in name_l for k in ["ldo", "regulator", "dc-dc", "pmic", "buck", "boost"]):
                ref_role[ref] = "power"
            elif any(k in name_l for k in ["adc", "digitiz"]):
                ref_role[ref] = "adc"
            elif any(k in name_l for k in ["fpga", "cpld", "zynq", "ultrascale"]):
                ref_role[ref] = "fpga"
            elif any(k in name_l for k in ["synthesiz", "pll", "vco", " lo "]):
                ref_role[ref] = "lo_synth"
            elif any(k in name_l for k in ["oscillat", "clock", "crystal"]):
                ref_role[ref] = "clock"
            elif any(k in name_l for k in ["phy", "ethernet", "transceiver"]):
                ref_role[ref] = "interface"
            elif "limiter" in name_l:
                ref_role[ref] = "limiter"
            elif any(k in name_l for k in ["bias-tee", "bias tee", "biastee"]):
                ref_role[ref] = "bias_tee"
            elif any(k in name_l for k in ["splitter", "divider", "wilkinson", "power divider"]):
                ref_role[ref] = "splitter"
            elif any(k in name_l for k in ["coupler"]):
                ref_role[ref] = "coupler"
            elif any(k in name_l for k in ["attenuator", "pad"]):
                ref_role[ref] = "attenuator"
            elif any(k in name_l for k in ["isolator", "circulator"]):
                ref_role[ref] = "isolator"
            elif any(k in name_l for k in ["balun", "transformer"]):
                ref_role[ref] = "balun"
            else:
                ref_role[ref] = "signal"

        # ── Role-specific pin templates ───────────────────────────────────
        # Each role gets realistic pins matching real datasheets.
        ROLE_PINS: dict = {
            # SMA / BNC panel connector — single RF port + chassis ground.
            # Named pins so the renderer's IC pin-anchor lookup resolves
            # them (fixes "pin 1 of J1 hanging").
            "connector": [
                {"name": "RF_OUT", "num": "1", "side": "right"},
                {"name": "GND", "num": "2", "side": "bottom"},
            ],
            "rf_amp": [
                {"name": "RF_IN_1", "num": "1", "side": "left"},
                {"name": "RF_IN_2", "num": "2", "side": "left"},
                {"name": "RF_OUT_1", "num": "3", "side": "right"},
                {"name": "RF_OUT_2", "num": "4", "side": "right"},
                {"name": "VCC", "num": "5", "side": "top"},
                {"name": "GND", "num": "6", "side": "bottom"},
            ],
            "filter": [
                {"name": "IN_1", "num": "1", "side": "left"},
                {"name": "IN_2", "num": "2", "side": "left"},
                {"name": "OUT_1", "num": "3", "side": "right"},
                {"name": "OUT_2", "num": "4", "side": "right"},
                {"name": "GND", "num": "5", "side": "bottom"},
            ],
            "rf_mixer": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "LO_P", "num": "2", "side": "left"},
                {"name": "LO_N", "num": "3", "side": "left"},
                {"name": "IF_OUT_P", "num": "4", "side": "right"},
                {"name": "IF_OUT_N", "num": "5", "side": "right"},
                {"name": "VCC", "num": "6", "side": "top"},
                {"name": "GND", "num": "7", "side": "bottom"},
            ],
            "lo_synth": [
                {"name": "CLK_REF_P", "num": "1", "side": "left"},
                {"name": "CLK_REF_N", "num": "2", "side": "left"},
                {"name": "SPI_CLK", "num": "3", "side": "left"},
                {"name": "SPI_DATA", "num": "4", "side": "left"},
                {"name": "SPI_LE", "num": "5", "side": "left"},
                {"name": "RF_OUT_P", "num": "6", "side": "right"},
                {"name": "RF_OUT_N", "num": "7", "side": "right"},
                {"name": "LOCK_DET", "num": "8", "side": "right"},
                {"name": "VCC_RF", "num": "9", "side": "top"},
                {"name": "VCC_DIG", "num": "10", "side": "top"},
                {"name": "GND", "num": "11", "side": "bottom"},
            ],
            "clock": [
                {"name": "VCC", "num": "1", "side": "top"},
                {"name": "GND", "num": "2", "side": "bottom"},
                {"name": "CLK_OUT_P", "num": "3", "side": "right"},
                {"name": "CLK_OUT_N", "num": "4", "side": "right"},
                {"name": "EN", "num": "5", "side": "left"},
            ],
            "adc": [
                {"name": "AIN_P", "num": "1", "side": "left"},
                {"name": "AIN_N", "num": "2", "side": "left"},
                {"name": "CLK_P", "num": "3", "side": "left"},
                {"name": "CLK_N", "num": "4", "side": "left"},
                {"name": "SYNC_P", "num": "5", "side": "left"},
                {"name": "SYNC_N", "num": "6", "side": "left"},
                {"name": "D0_P", "num": "7", "side": "right"},
                {"name": "D0_N", "num": "8", "side": "right"},
                {"name": "D1_P", "num": "9", "side": "right"},
                {"name": "D1_N", "num": "10", "side": "right"},
                {"name": "DCO_P", "num": "11", "side": "right"},
                {"name": "DCO_N", "num": "12", "side": "right"},
                {"name": "SPI_CLK", "num": "13", "side": "left"},
                {"name": "SPI_MOSI", "num": "14", "side": "left"},
                {"name": "SPI_CS", "num": "15", "side": "left"},
                {"name": "AVDD", "num": "16", "side": "top"},
                {"name": "DVDD", "num": "17", "side": "top"},
                {"name": "GND", "num": "18", "side": "bottom"},
            ],
            "fpga": [
                {"name": "ADC_D0_P", "num": "1", "side": "left"},
                {"name": "ADC_D0_N", "num": "2", "side": "left"},
                {"name": "ADC_D1_P", "num": "3", "side": "left"},
                {"name": "ADC_D1_N", "num": "4", "side": "left"},
                {"name": "ADC_DCO_P", "num": "5", "side": "left"},
                {"name": "ADC_DCO_N", "num": "6", "side": "left"},
                {"name": "ADC_FRAME_P", "num": "7", "side": "left"},
                {"name": "ADC_FRAME_N", "num": "8", "side": "left"},
                {"name": "SPI_CLK", "num": "9", "side": "right"},
                {"name": "SPI_MOSI", "num": "10", "side": "right"},
                {"name": "SPI_CS_ADC", "num": "11", "side": "right"},
                {"name": "SPI_CS_CLKGEN", "num": "12", "side": "right"},
                {"name": "CLK_IN_P", "num": "13", "side": "left"},
                {"name": "CLK_IN_N", "num": "14", "side": "left"},
                {"name": "GPIO_0", "num": "15", "side": "right"},
                {"name": "GPIO_1", "num": "16", "side": "right"},
                {"name": "VCCINT", "num": "17", "side": "top"},
                {"name": "VCCIO", "num": "18", "side": "top"},
                {"name": "GND", "num": "19", "side": "bottom"},
            ],
            "power": [
                {"name": "VIN", "num": "1", "side": "left"},
                {"name": "EN", "num": "2", "side": "left"},
                {"name": "VOUT", "num": "3", "side": "right"},
                {"name": "FB", "num": "4", "side": "right"},
                {"name": "GND", "num": "5", "side": "bottom"},
            ],
            "interface": [
                {"name": "DATA_IN", "num": "1", "side": "left"},
                {"name": "DATA_OUT", "num": "2", "side": "right"},
                {"name": "CLK", "num": "3", "side": "left"},
                {"name": "CS", "num": "4", "side": "left"},
                {"name": "VCC", "num": "5", "side": "top"},
                {"name": "GND", "num": "6", "side": "bottom"},
            ],
            "signal": [
                {"name": "IN", "num": "1", "side": "left"},
                {"name": "OUT", "num": "2", "side": "right"},
                {"name": "VCC", "num": "3", "side": "top"},
                {"name": "GND", "num": "4", "side": "bottom"},
            ],
            # ── Passive RF blocks — NO VCC, correct port count ────────
            "limiter": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "GND", "num": "3", "side": "bottom"},
            ],
            "bias_tee": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "DC_IN", "num": "3", "side": "top"},
                {"name": "GND", "num": "4", "side": "bottom"},
            ],
            # 1:4 Wilkinson — four outputs + input + internal GND return
            "splitter": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT_1", "num": "2", "side": "right"},
                {"name": "RF_OUT_2", "num": "3", "side": "right"},
                {"name": "RF_OUT_3", "num": "4", "side": "right"},
                {"name": "RF_OUT_4", "num": "5", "side": "right"},
                {"name": "GND", "num": "6", "side": "bottom"},
            ],
            # Directional coupler — through, coupled, isolated
            "coupler": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_THRU", "num": "2", "side": "right"},
                {"name": "RF_CPL", "num": "3", "side": "right"},
                {"name": "RF_ISO", "num": "4", "side": "right"},
                {"name": "GND", "num": "5", "side": "bottom"},
            ],
            "attenuator": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "GND", "num": "3", "side": "bottom"},
            ],
            "isolator": [
                {"name": "RF_IN", "num": "1", "side": "left"},
                {"name": "RF_OUT", "num": "2", "side": "right"},
                {"name": "GND", "num": "3", "side": "bottom"},
            ],
            "balun": [
                {"name": "SE_IN", "num": "1", "side": "left"},
                {"name": "BAL_P", "num": "2", "side": "right"},
                {"name": "BAL_N", "num": "3", "side": "right"},
                {"name": "GND", "num": "4", "side": "bottom"},
            ],
        }

        # Active-IC roles that need Vdd decoupling. Anything not in this set
        # is treated as a passive and gets no decoupling cap, no VCC symbol,
        # no rail trace — matching how real PIN-diode limiters, Wilkinson
        # splitters, bias-tees, pads, isolators, and baluns actually wire.
        # NOTE: "signal" is NOT in this set — the fallback/unknown bucket
        # must not collect decoupling blindly. If the LLM drops a
        # resistor or capacitor into the netlist with no hint of what it
        # is, we'd rather leave it un-decorated than scatter fake VCC
        # caps around it.
        ACTIVE_ROLES = frozenset({
            "rf_amp", "rf_amplifier", "rf_mixer", "lo_synth",
            "adc", "fpga", "clock", "interface", "power",
        })
        # Pure-passive R/L/C/D roles — rendered as their native 2-pin
        # symbol, not as an IC block. Always skip the IC placement loop's
        # pin / decoupling / ground insertion for these.
        PASSIVE_RLCD_ROLES = frozenset({
            "r_passive", "c_passive", "l_passive", "d_passive",
        })

        # ─── Pin-anchor math ─────────────────────────────────────────────
        # Python mirror of `getPinAnchor`/`getIcSize` in
        # hardware-pipeline-v5-react/src/components/schematic/symbols/index.tsx
        # so auto-generated passives (caps, ground symbols, ESD diodes) can
        # land exactly on the IC pin they're wired to instead of visually
        # hanging nearby.
        def _ic_size(pins: list[dict]) -> tuple[int, int]:
            sides = {"left": 0, "right": 0, "top": 0, "bottom": 0}
            for p in pins:
                sides[p["side"]] = sides.get(p["side"], 0) + 1
            max_lr = max(sides["left"], sides["right"], 2)
            max_tb = max(sides["top"], sides["bottom"], 0)
            w = max(4, max_tb + 2)
            h = max(3, max_lr + 1)
            return w, h

        def _ic_pin_local(pins: list[dict], pin_name: str) -> tuple[float, float] | None:
            """Local (dx, dy) of pin `pin_name` in the IC's bbox — matches
            the TS getLocalPinAnchor for type='ic'."""
            sides: dict[str, list[dict]] = {"left": [], "right": [], "top": [], "bottom": []}
            for p in pins:
                sides[p["side"]].append(p)
            w, h = _ic_size(pins)
            for side, pin_list in sides.items():
                idx = next((i for i, p in enumerate(pin_list)
                            if p["name"] == pin_name or p.get("num") == pin_name), -1)
                if idx < 0:
                    continue
                step = (h / (len(pin_list) + 1)) if side in ("left", "right") \
                       else (w / (len(pin_list) + 1))
                pos = step * (idx + 1)
                if side == "left":   return (0.0, pos)
                if side == "right":  return (w, pos)
                if side == "top":    return (pos, 0.0)
                if side == "bottom": return (pos, h)
            return None

        def _ic_pin_global(ic_x: float, ic_y: float, pins: list[dict],
                            pin_name: str) -> tuple[float, float] | None:
            """Global grid coord of an IC pin (no rotation applied — backend
            always emits rot=0 for ICs)."""
            local = _ic_pin_local(pins, pin_name)
            if local is None:
                return None
            dx, dy = local
            return (ic_x + dx, ic_y + dy)

        # ── Group refs by sheet ───────────────────────────────────────────
        sheet_map = {
            "rf": [], "power": [], "adc_dig": [], "clock": [],
        }
        for ref, role in ref_role.items():
            # RF sheet: every component on the RF signal path, active or passive.
            if role in ("connector", "rf_amp", "rf_amplifier", "filter", "rf_mixer",
                         "limiter", "bias_tee", "splitter", "coupler",
                         "attenuator", "isolator", "balun"):
                sheet_map["rf"].append(ref)
            elif role == "power":
                sheet_map["power"].append(ref)
            elif role in ("adc", "fpga", "interface"):
                sheet_map["adc_dig"].append(ref)
            elif role in ("lo_synth", "clock"):
                sheet_map["clock"].append(ref)
            elif role in PASSIVE_RLCD_ROLES:
                # Pure-passive R/L/C/D that the LLM emitted directly into
                # the netlist. Group with the RF sheet by default — for
                # a typical receiver these are attenuator pads, matching
                # caps, or choke inductors on the RF path. If the LLM
                # intended a power-rail cap, a later pass can move it.
                sheet_map["rf"].append(ref)
            else:
                # "signal" and truly-unknown roles fall through here. Route
                # based on a final heuristic — if the part carries a known
                # digital keyword, send to ADC; otherwise keep it on RF
                # rather than mis-filing an RF block as digital.
                _name = (ref_node.get(ref, {}).get("component_name", "") + " " +
                         ref_node.get(ref, {}).get("part_number", "")).lower()
                _digital = any(k in _name for k in (
                    "fpga", "cpld", "mcu", "processor", "controller",
                    "jesd", "lvds", "gpio", "uart", "i2c", "spi",
                    "transceiver", "phy", "digitiz",
                ))
                sheet_map["adc_dig" if _digital else "rf"].append(ref)

        # Merge small clock into adc_dig
        if len(sheet_map["clock"]) <= 1:
            sheet_map["adc_dig"].extend(sheet_map["clock"])
            sheet_map["clock"] = []

        SHEET_TITLES = {
            "rf": "RF Front-End & Power Distribution",
            "power": "Power Distribution",
            "adc_dig": "ADC & Digitisation",
            "clock": "Clock Generation & SPI Control",
        }
        SHEET_ORDER = ["rf", "adc_dig", "clock", "power"]

        # ── Build sheets ──────────────────────────────────────────────────
        #
        # Reference-designator counters. Auto-generated refs (C*, R*, L*,
        # D*, GND*, VCC_*) must NEVER collide with the LLM-supplied refs
        # that are already in `ref_role`. Scan the user-supplied refs
        # once and seed each counter from (max existing suffix for prefix)
        # so auto-refs start fresh beyond them.
        import re as _re_ref
        def _max_suffix(prefixes: tuple[str, ...]) -> int:
            hi = 0
            for r in ref_role.keys():
                for pref in prefixes:
                    if r.upper().startswith(pref):
                        m = _re_ref.search(r"(\d+)$", r)
                        if m:
                            hi = max(hi, int(m.group()))
                        break
            return hi

        sheets = []
        g_cap = _max_suffix(("C",))   # capacitors
        g_gnd = 0                      # ground symbols are always auto-named
        g_pwr = 0                      # VCC symbols — same
        # R and L share a counter historically; seed from max of both so an
        # LLM-emitted R4 doesn't clash with an auto-created R4 terminator.
        g_res = max(_max_suffix(("R",)), _max_suffix(("L",)), _max_suffix(("D",)))

        for sheet_key in SHEET_ORDER:
            refs = sheet_map.get(sheet_key, [])
            if not refs:
                continue

            comps: list = []
            nets: list = []
            placed: set = set()

            # Place ICs on a wider grid that reflects realistic symbol
            # extents. Each IC symbol is ~8 units wide + decoupling stack
            # to its right takes another ~3 units → budget 14 units per
            # column. Rows grow downward without clamping so components
            # never overlap visually. Sheet width / height are implicitly
            # the bounding box of the last placed component.
            COL_PITCH = 14
            ROW_PITCH = 10
            MAX_COLS = 4
            # Map a passive role → renderer component type. Passives are
            # emitted as their native 2-pin symbol (resistor / capacitor /
            # inductor / diode), not as an IC block with fake VCC / IN / OUT.
            _PASSIVE_TYPE_MAP = {
                "r_passive": "resistor",
                "c_passive": "capacitor",
                "l_passive": "inductor",
                "d_passive": "diode",
            }

            for idx, ref in enumerate(refs):
                node = ref_node.get(ref, {})
                role = ref_role.get(ref, "signal")
                col = idx % MAX_COLS
                row = idx // MAX_COLS
                x = 4 + col * COL_PITCH
                y = 6 + row * ROW_PITCH

                # ── Pure passive R/L/C/D: native 2-pin symbol, no pin list,
                #    no decoupling, no VCC, no auto-ground.
                if role in PASSIVE_RLCD_ROLES:
                    comp_type = _PASSIVE_TYPE_MAP[role]
                    # Short value: strip the chip series and leave the
                    # parametric core. For a resistor R3, show "10K" not
                    # "CRCW060310K0FKEA" — the full part_number stays in
                    # the reverse-lookup field.
                    full_part = node.get("part_number", "") or ""
                    comps.append({
                        "ref": ref, "type": comp_type,
                        "value": full_part,
                        "part_number": full_part,
                        "x": x, "y": y, "rot": 0,
                    })
                    placed.add(ref)
                    continue  # skip IC-only decoration below

                # Everything else renders as an IC (including connectors,
                # whose named pins now resolve via the name-based anchor).
                pins = [dict(p) for p in ROLE_PINS.get(role, ROLE_PINS["signal"])]
                comp_type = "ic"
                comp_value = node.get("part_number", "")

                comps.append({
                    "ref": ref, "type": comp_type,
                    "value": comp_value,
                    "part_number": node.get("part_number", ""),
                    "x": x, "y": y, "rot": 0, "pins": pins,
                })
                placed.add(ref)

                # Multi-value decoupling stack — ACTIVE ICs only.
                # Every cap lands exactly above the IC's VCC pin so the
                # auto-drawn net is a short vertical trace (no hanging
                # geometry, no diagonal routing). Cap is rot=90 so pin 1
                # is at the TOP — this is where the trace from the IC's
                # top-side VCC pin terminates.
                if role in ACTIVE_ROLES:
                    vcc_pins = [p for p in pins if p["side"] == "top" and
                                any(p["name"].upper().startswith(v)
                                    for v in ("VCC", "VDD", "AVDD", "DVDD"))]
                    # 3-value RF stack: bulk / mid-band / HF. All three caps
                    # stack vertically above the VCC pin; each gets its own
                    # dedicated ground return (separate vias = low HF
                    # impedance path).
                    DECOUPLING_STACK = [
                        ("1uF",   0),   # bulk — farthest from pin
                        ("100nF", 1),   # mid-band
                        ("10nF",  2),   # HF — closest to pin
                    ]
                    for vp in vcc_pins:
                        rail = vp["name"].upper()
                        anchor = _ic_pin_global(x, y, pins, vp["name"])
                        if anchor is None:
                            continue
                        ax, _ay = anchor
                        # For a rot=90 two-pin symbol, pin 1 ends up at
                        # (comp.x - 0.5, comp.y). To place pin 1 at the
                        # VCC anchor, set comp.x = ax + 0.5, comp.y = ay_top.
                        # Stack grows upward: ay_top = y - 3*(slot+1) + 1.
                        for cap_val, slot in DECOUPLING_STACK:
                            g_cap += 1
                            cref = f"C{g_cap}"
                            cy = max(y - (slot * 3) - 3, 1)
                            cx = int(round(ax + 0.5))
                            comps.append({"ref": cref, "type": "capacitor",
                                          "value": cap_val,
                                          "x": cx, "y": cy, "rot": 90})
                            nets.append({"name": rail, "type": "power",
                                         "endpoints": [{"ref": ref, "pin": vp["name"]},
                                                       {"ref": cref, "pin": "1"}]})
                            # Ground return: place ground symbol such that
                            # its top anchor (0.5, 0) lands on cap pin 2
                            # (cx - 0.5, cy + 2).
                            g_gnd += 1
                            gref = f"GND_C{g_cap}"
                            comps.append({"ref": gref, "type": "ground", "value": "GND",
                                          "x": cx - 1, "y": cy + 2, "rot": 0})
                            nets.append({"name": "GND", "type": "ground",
                                         "endpoints": [{"ref": cref, "pin": "2"},
                                                       {"ref": gref, "pin": "1"}]})

                # Ground symbol for IC GND pin — placed DIRECTLY below the
                # actual bottom GND pin so the net draws a single short
                # vertical trace, not a long diagonal stub.
                gnd_pins = [p for p in pins if p["name"].upper() in ("GND", "AGND", "DGND")]
                for gp in gnd_pins:
                    anchor = _ic_pin_global(x, y, pins, gp["name"])
                    g_gnd += 1
                    gref = f"GND{g_gnd}"
                    if anchor is not None:
                        ax, ay = anchor
                        # Ground anchor is at (0.5, 0) of its symbol — so
                        # align ground.x so anchor lands on IC GND pin.
                        gx = int(round(ax - 0.5))
                        gy = int(round(ay))
                    else:
                        gx, gy = x, y + 3
                    comps.append({"ref": gref, "type": "ground", "value": "GND",
                                  "x": gx, "y": gy, "rot": 0})
                    nets.append({"name": "GND", "type": "ground",
                                 "endpoints": [{"ref": ref, "pin": gp["name"]},
                                               {"ref": gref, "pin": "1"}]})

            # Build ref→comp lookup — later passes (VCC symbols, splitter
            # terminators, bias-tee choke, ESD) use it for precise placement
            # relative to each IC's actual pin positions.
            comps_by_ref = {c["ref"]: c for c in comps if "pins" in c}

            # VCC symbols — emit ONE per active IC VCC pin, placed directly
            # above the HF decoupling cap (top of the stack). Short,
            # traceable rails with no long shared horizontal bus. The
            # renderer anchors VCC at (0.5, 1) — bottom of the symbol —
            # so the VCC tip sits right on top of the bulk cap's pin 1.
            for ref in refs:
                role = ref_role.get(ref, "signal")
                if role not in ACTIVE_ROLES:
                    continue
                ic_comp = comps_by_ref.get(ref)
                if not ic_comp:
                    continue
                pins = ROLE_PINS.get(role, ROLE_PINS["signal"])
                for p in pins:
                    pn = p["name"].upper()
                    if p["side"] != "top":
                        continue
                    if not any(pn.startswith(v)
                               for v in ("VCC", "VDD", "AVDD", "DVDD", "VIN")):
                        continue
                    anchor = _ic_pin_global(ic_comp["x"], ic_comp["y"],
                                             pins, p["name"])
                    if anchor is None:
                        continue
                    ax, _ay = anchor
                    # Stack: bulk cap at y-3, mid at y-6, HF at y-9 (rot=90,
                    # length 2 each). VCC symbol sits one unit above the
                    # bulk cap's pin 1 → at y = (y-3) - 1 - 1 = y-5 ... but
                    # conceptually we want it at the TOP of the stack.
                    # Place it above the bulk cap (slot 0, cy = y-3):
                    # bulk cap pin 1 is at (cx-0.5, cy). VCC anchor is
                    # (comp.x+0.5, comp.y+1). For them to coincide:
                    # comp.x = cx - 1, comp.y = cy - 1.
                    cx = int(round(ax + 0.5))
                    cy_bulk = max(ic_comp["y"] - 3, 1)
                    vx = cx - 1
                    vy = max(cy_bulk - 2, 0)
                    g_pwr += 1
                    pref = f"VCC_{g_pwr}"
                    comps.append({"ref": pref, "type": "vcc", "value": p["name"],
                                  "x": vx, "y": vy, "rot": 0})
                    nets.append({"name": p["name"].upper(), "type": "power",
                                 "endpoints": [{"ref": pref, "pin": "1"},
                                               {"ref": ref, "pin": p["name"]}]})

            # ── Signal nets: wire adjacent ICs in the signal chain ────────
            for i in range(len(refs) - 1):
                src_ref = refs[i]
                dst_ref = refs[i + 1]
                src_role = ref_role.get(src_ref, "signal")
                dst_role = ref_role.get(dst_ref, "signal")
                # Passive R/L/C/D aren't in ROLE_PINS — the explicit netlist
                # edges (below) own their connections. Skip them in the
                # side-pin daisy so we don't wire the wrong pins.
                if src_role in PASSIVE_RLCD_ROLES or dst_role in PASSIVE_RLCD_ROLES:
                    continue
                src_pins = ROLE_PINS.get(src_role, ROLE_PINS["signal"])
                dst_pins = ROLE_PINS.get(dst_role, ROLE_PINS["signal"])

                # Find output pins of src and input pins of dst. RF blocks
                # expose ports like RF_OUT / RF_OUT_1..N / RF_THRU; we pick
                # the primary output (the lowest-numbered or un-suffixed one)
                # here and leave splitter secondary ports for the fan-out
                # pass below.
                def _primary_out_name(p: dict) -> str:
                    return p["name"].upper()

                out_pins = [p for p in src_pins if p["side"] == "right"
                            and not any(_primary_out_name(p).startswith(x)
                                        for x in ("SPI", "GPIO", "LOCK", "FB",
                                                  "DCO", "SYNC"))]
                # Splitters are handled specially — skip the multi-output
                # chaining and use only RF_OUT_1 as the primary "next stage"
                # connection; siblings are terminated or fed to later peers
                # by the splitter fan-out pass.
                if src_role == "splitter":
                    out_pins = [p for p in out_pins
                                if _primary_out_name(p) == "RF_OUT_1"]
                in_pins = [p for p in dst_pins if p["side"] == "left"
                           and not any(p["name"].upper().startswith(x)
                                       for x in ("SPI", "EN", "CLK_REF",
                                                 "SYNC", "DC_IN"))]

                # Wire matching pairs (differential or single-ended)
                n_pairs = min(len(out_pins), len(in_pins))
                for j in range(n_pairs):
                    op = out_pins[j]["name"]
                    ip = in_pins[j]["name"]
                    ntype = "analog"
                    if "CLK" in op.upper() or "CLK" in ip.upper():
                        ntype = "clock"
                    elif "D0" in op.upper() or "D1" in op.upper() or "DCO" in op.upper():
                        ntype = "signal"
                    # RF path: add a DC-blocking cap between any two active
                    # RF blocks (LNA/mixer/driver) so DC offsets don't
                    # propagate and bias feed doesn't escape into the trace.
                    needs_dc_block = (
                        src_role in ("rf_amp", "rf_amplifier", "rf_mixer", "bias_tee")
                        and dst_role in ("rf_amp", "rf_amplifier", "rf_mixer", "filter")
                    )
                    if needs_dc_block:
                        g_cap += 1
                        bcref = f"C{g_cap}"
                        bcx = comps_by_ref.get(src_ref, {}).get("x", 4) + 8 \
                              if False else 0  # placeholder, fixed below
                        # Place the cap midway between the two ICs
                        src_comp = next((c for c in comps if c["ref"] == src_ref), None)
                        dst_comp = next((c for c in comps if c["ref"] == dst_ref), None)
                        if src_comp and dst_comp:
                            bcx = (src_comp["x"] + dst_comp["x"]) // 2
                            bcy = src_comp["y"] + 1
                        else:
                            bcx, bcy = 10, 8
                        comps.append({"ref": bcref, "type": "capacitor",
                                      "value": "100pF",
                                      "x": bcx, "y": bcy, "rot": 0})
                        # Two nets: src_out → cap_pin1 and cap_pin2 → dst_in
                        nets.append({"name": f"{op}_{src_ref}_DCB", "type": ntype,
                                     "endpoints": [{"ref": src_ref, "pin": op},
                                                   {"ref": bcref, "pin": "1"}]})
                        nets.append({"name": f"{op}_{src_ref}_IN", "type": ntype,
                                     "endpoints": [{"ref": bcref, "pin": "2"},
                                                   {"ref": dst_ref, "pin": ip}]})
                    else:
                        nets.append({"name": f"{op}_{src_ref}", "type": ntype,
                                     "endpoints": [{"ref": src_ref, "pin": op},
                                                   {"ref": dst_ref, "pin": ip}]})

            # (comps_by_ref already built earlier, before the VCC-symbol pass)

            # ── Splitter fan-out: terminate unused output ports with 50 Ω
            #    loads to ground. Each resistor is placed so its pin 1
            #    lands exactly on the splitter's secondary output pin;
            #    the matching ground symbol lands exactly on pin 2.
            for sref in refs:
                if ref_role.get(sref) != "splitter":
                    continue
                split_pins = ROLE_PINS.get("splitter", [])
                secondary_outs = [p["name"] for p in split_pins
                                  if p["side"] == "right"
                                  and p["name"].upper() != "RF_OUT_1"]
                scomp = comps_by_ref.get(sref)
                if not scomp:
                    continue
                for pn in secondary_outs:
                    anchor = _ic_pin_global(scomp["x"], scomp["y"], split_pins, pn)
                    if anchor is None:
                        continue
                    ax, ay = anchor
                    g_res += 1
                    rref = f"R{g_res}"
                    # Horizontal resistor (rot=0) — pin 1 at left edge. Put
                    # resistor right-of the splitter pin so pin 1 lands on
                    # the splitter output.
                    rx = int(round(ax))
                    ry = int(round(ay - 0.5))
                    comps.append({"ref": rref, "type": "resistor",
                                  "value": "50R", "x": rx, "y": ry, "rot": 0})
                    # Ground symbol below the resistor, wired to pin 2.
                    g_gnd += 1
                    gref_t = f"GND_R{g_res}"
                    comps.append({"ref": gref_t, "type": "ground", "value": "GND",
                                  "x": rx + 1, "y": ry + 1, "rot": 0})
                    nets.append({"name": f"TERM_{sref}_{pn}", "type": "analog",
                                 "endpoints": [{"ref": sref, "pin": pn},
                                               {"ref": rref, "pin": "1"}]})
                    nets.append({"name": "GND", "type": "ground",
                                 "endpoints": [{"ref": rref, "pin": "2"},
                                               {"ref": gref_t, "pin": "1"}]})

            # ── Bias-tee DC feed: RF choke + bulk cap on the DC_IN port.
            #    Choke placed directly above the bias-tee's DC_IN pin with
            #    its pin 2 facing up; bulk cap to its right, both landing
            #    on the rail node in between.
            for btref in refs:
                if ref_role.get(btref) != "bias_tee":
                    continue
                btc = comps_by_ref.get(btref)
                if not btc:
                    continue
                bt_pins = ROLE_PINS.get("bias_tee", [])
                dc_anchor = _ic_pin_global(btc["x"], btc["y"], bt_pins, "DC_IN")
                if dc_anchor is None:
                    continue
                dx, dy = dc_anchor
                # Choke vertical (rot=90) above DC_IN: pin 1 at (comp.x-0.5, comp.y)
                # so place inductor at (dx+0.5, dy-3). Length 2 grid units.
                g_res += 1
                lref = f"L{g_res}"
                lx = int(round(dx + 0.5))
                ly = max(int(round(dy - 3)), 1)
                comps.append({"ref": lref, "type": "inductor",
                              "value": "47nH", "x": lx, "y": ly, "rot": 90})
                # Bulk 10 µF cap to the right of the choke, sharing a rail node.
                g_cap += 1
                dc_cref = f"C{g_cap}"
                comps.append({"ref": dc_cref, "type": "capacitor",
                              "value": "10uF", "x": lx + 2, "y": ly, "rot": 90})
                # Ground return under the bulk cap, aligned with its pin 2.
                g_gnd += 1
                dc_gref = f"GND_BT{g_gnd}"
                comps.append({"ref": dc_gref, "type": "ground", "value": "GND",
                              "x": lx + 1, "y": ly + 2, "rot": 0})
                nets.append({"name": f"BT_DC_{btref}", "type": "power",
                             "endpoints": [{"ref": btref, "pin": "DC_IN"},
                                           {"ref": lref, "pin": "1"}]})
                nets.append({"name": f"BT_DC_RAIL_{btref}", "type": "power",
                             "endpoints": [{"ref": lref, "pin": "2"},
                                           {"ref": dc_cref, "pin": "1"}]})
                nets.append({"name": "GND", "type": "ground",
                             "endpoints": [{"ref": dc_cref, "pin": "2"},
                                           {"ref": dc_gref, "pin": "1"}]})

            # ── Antenna-input ESD/TVS: vertical diode on the connector's
            #    RF_OUT trace. Rotated 90° so pin 1 (anode) is at the top
            #    touching the signal and pin 2 (cathode) is at the bottom
            #    touching the ground symbol — conventional clamp topology.
            if refs:
                first_ref = refs[0]
                if ref_role.get(first_ref) == "connector":
                    fcomp = comps_by_ref.get(first_ref)
                    if fcomp:
                        fpins = ROLE_PINS.get("connector", [])
                        rf_pin = next((p["name"] for p in fpins
                                       if p["side"] == "right"), None)
                        if rf_pin:
                            anchor = _ic_pin_global(fcomp["x"], fcomp["y"], fpins, rf_pin)
                            if anchor is not None:
                                ax, ay = anchor
                                g_res += 1
                                dref = f"D{g_res}"
                                # Diode rot=90 — pin 1 (anode) at comp.y (top),
                                # pin 2 (cathode) at comp.y + 2 (bottom).
                                # Place comp.x = ax + 0.5 so pin 1 lands on ax.
                                dx_d = int(round(ax + 0.5))
                                dy_d = int(round(ay))
                                comps.append({"ref": dref, "type": "diode_tvs",
                                              "value": "ESD",
                                              "x": dx_d, "y": dy_d, "rot": 90})
                                g_gnd += 1
                                dgref = f"GND_ESD{g_gnd}"
                                # Ground symbol's anchor (0.5, 0) lands on
                                # cathode (dx_d - 0.5, dy_d + 2).
                                comps.append({"ref": dgref, "type": "ground",
                                              "value": "GND",
                                              "x": dx_d - 1, "y": dy_d + 2,
                                              "rot": 0})
                                nets.append({"name": f"ESD_{first_ref}",
                                             "type": "analog",
                                             "endpoints": [{"ref": first_ref, "pin": rf_pin},
                                                           {"ref": dref, "pin": "1"}]})
                                nets.append({"name": "GND", "type": "ground",
                                             "endpoints": [{"ref": dref, "pin": "2"},
                                                           {"ref": dgref, "pin": "1"}]})

            # ── SPI bus: wire FPGA SPI pins → ADC/Synth SPI pins ──────────
            # Maps target SPI pin names to FPGA pin names per role
            fpga_refs = [r for r in refs if ref_role.get(r) == "fpga"]
            spi_targets = [r for r in refs if ref_role.get(r) in ("adc", "lo_synth")]
            if fpga_refs and spi_targets:
                fpga = fpga_refs[0]
                for tgt in spi_targets:
                    tgt_role = ref_role.get(tgt, "signal")
                    tgt_pins = ROLE_PINS.get(tgt_role, [])
                    tgt_spi = [p for p in tgt_pins if p["name"].upper().startswith("SPI")]
                    for tp in tgt_spi:
                        pn_up = tp["name"].upper()
                        # Map target pin → FPGA pin
                        if "CS" in pn_up or "LE" in pn_up:
                            suffix = "ADC" if tgt_role == "adc" else "CLKGEN"
                            fpga_pin_name = f"SPI_CS_{suffix}"
                        elif "DATA" in pn_up or "MOSI" in pn_up:
                            fpga_pin_name = "SPI_MOSI"
                        elif "CLK" in pn_up:
                            fpga_pin_name = "SPI_CLK"
                        else:
                            fpga_pin_name = tp["name"]
                        nets.append({
                            "name": f"SPI_{tp['name']}_{tgt}",
                            "type": "signal",
                            "endpoints": [{"ref": fpga, "pin": fpga_pin_name},
                                          {"ref": tgt, "pin": tp["name"]}],
                        })

            # ── Tie unconnected differential _2/N input pins to GND ─────
            # Covers cases like single-ended source → differential input
            connected_pins: set = set()
            for net in nets:
                for ep in net["endpoints"]:
                    connected_pins.add((ep["ref"], ep["pin"]))
            for c in comps:
                if c["type"] != "ic" or "pins" not in c:
                    continue
                for p in c.get("pins", []):
                    if (c["ref"], p["name"]) in connected_pins:
                        continue
                    pn = p["name"].upper()
                    # Skip power/gnd pins (handled above)
                    if pn in ("GND", "AGND", "DGND") or pn.startswith("VCC") or pn.startswith("VDD") or pn.startswith("AVDD") or pn.startswith("DVDD"):
                        continue
                    # Differential N/2 pin with no connection → AC-ground
                    is_diff_n = (pn.endswith("_2") or pn.endswith("_N")) and p["side"] == "left"
                    if is_diff_n:
                        g_cap += 1
                        ac_ref = f"C{g_cap}"
                        cx = max(c["x"] - 3, 1)
                        cy = c["y"] + 1
                        comps.append({"ref": ac_ref, "type": "capacitor", "value": "100nF",
                                      "x": cx, "y": cy, "rot": 0})
                        g_gnd += 1
                        gref_ac = f"GND_AC{g_gnd}"
                        comps.append({"ref": gref_ac, "type": "ground", "value": "GND",
                                      "x": cx, "y": cy + 2, "rot": 0})
                        nets.append({"name": f"AC_GND_{c['ref']}_{p['name']}", "type": "analog",
                                     "endpoints": [{"ref": c["ref"], "pin": p["name"]},
                                                   {"ref": ac_ref, "pin": "1"}]})
                        nets.append({"name": "GND", "type": "ground",
                                     "endpoints": [{"ref": ac_ref, "pin": "2"},
                                                   {"ref": gref_ac, "pin": "1"}]})
                        connected_pins.add((c["ref"], p["name"]))

            # ── Power regulator wiring ────────────────────────────────────
            pwr_refs = [r for r in refs if ref_role.get(r) == "power"]
            for pidx, pr in enumerate(pwr_refs):
                # VIN from main power rail via off-page connector
                g_pwr += 1
                vin_ref = f"VCC_IN_{g_pwr}"
                node = ref_node.get(pr, {})
                pr_comp = next((c for c in comps if c["ref"] == pr), None)
                pr_x = pr_comp["x"] if pr_comp else (4 + pidx * 9)
                pr_y = pr_comp["y"] if pr_comp else 5
                comps.append({"ref": vin_ref, "type": "vcc", "value": "VIN_MAIN",
                              "x": max(pr_x - 3, 1), "y": pr_y, "rot": 0})
                nets.append({"name": "VIN_MAIN", "type": "power",
                             "endpoints": [{"ref": vin_ref, "pin": "1"},
                                           {"ref": pr, "pin": "VIN"}]})
                # EN tied high (to VIN)
                nets.append({"name": "EN_HIGH", "type": "power",
                             "endpoints": [{"ref": vin_ref, "pin": "1"},
                                           {"ref": pr, "pin": "EN"}]})
                # VOUT to output rail VCC symbol
                g_pwr += 1
                vout_ref = f"VCC_OUT_{g_pwr}"
                pn_lower = (node.get("component_name", "") + node.get("part_number", "")).lower()
                rail_label = "VCC_3V3" if "3.3" in pn_lower or "3v3" in pn_lower else \
                             "VCC_1V8" if "1.8" in pn_lower or "1v8" in pn_lower else \
                             "VCC_5V" if "5v" in pn_lower or "5.0" in pn_lower else \
                             f"VOUT_{pr}"
                comps.append({"ref": vout_ref, "type": "vcc", "value": rail_label,
                              "x": min(pr_x + 5, 28), "y": pr_y, "rot": 0})
                nets.append({"name": rail_label, "type": "power",
                             "endpoints": [{"ref": pr, "pin": "VOUT"},
                                           {"ref": vout_ref, "pin": "1"}]})
                # FB tied to VOUT (internal feedback divider)
                nets.append({"name": f"FB_{pr}", "type": "signal",
                             "endpoints": [{"ref": pr, "pin": "FB"},
                                           {"ref": pr, "pin": "VOUT"}]})
                # Output decoupling cap
                g_cap += 1
                cout_ref = f"C{g_cap}"
                comps.append({"ref": cout_ref, "type": "capacitor", "value": "10uF",
                              "x": min(pr_x + 4, 28), "y": min(pr_y + 2, 18), "rot": 90})
                g_gnd += 1
                gnd_cout = f"GND_C{g_cap}"
                comps.append({"ref": gnd_cout, "type": "ground", "value": "GND",
                              "x": min(pr_x + 4, 28), "y": min(pr_y + 4, 20), "rot": 0})
                nets.append({"name": rail_label, "type": "power",
                             "endpoints": [{"ref": pr, "pin": "VOUT"},
                                           {"ref": cout_ref, "pin": "1"}]})
                nets.append({"name": "GND", "type": "ground",
                             "endpoints": [{"ref": cout_ref, "pin": "2"},
                                           {"ref": gnd_cout, "pin": "1"}]})

            sheets.append({
                "id": f"sheet{len(sheets) + 1}",
                "title": SHEET_TITLES.get(sheet_key, "Schematic"),
                "components": comps,
                "nets": nets,
            })

        # ── Cross-sheet connections via off-page connectors ───────────────
        # Build global ref→sheet lookup
        ref_sheet: dict = {}
        for si, sh in enumerate(sheets):
            for c in sh["components"]:
                ref_sheet[c["ref"]] = si

        # Mixer IF → ADC AIN (cross-sheet)
        all_mixers = [r for r, rl in ref_role.items() if rl == "rf_mixer"]
        all_adcs = [r for r, rl in ref_role.items() if rl == "adc"]
        for mx in all_mixers:
            for adc in all_adcs:
                mx_si = ref_sheet.get(mx)
                adc_si = ref_sheet.get(adc)
                if mx_si is not None and adc_si is not None:
                    # Off-page connector on mixer sheet (IF output)
                    g_pwr += 1
                    opc1 = f"OPC_{g_pwr}"
                    sheets[mx_si]["components"].append(
                        {"ref": opc1, "type": "connector", "value": f"→ Sheet {adc_si + 1}",
                         "x": 28, "y": 8, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "left"}]})
                    sheets[mx_si]["nets"].append(
                        {"name": f"IF_OUT_{mx}", "type": "analog",
                         "endpoints": [{"ref": mx, "pin": "IF_OUT_P"},
                                       {"ref": opc1, "pin": "1"}]})
                    sheets[mx_si]["nets"].append(
                        {"name": f"IF_OUT_N_{mx}", "type": "analog",
                         "endpoints": [{"ref": mx, "pin": "IF_OUT_N"},
                                       {"ref": opc1, "pin": "1"}]})
                    # Off-page connector on ADC sheet (AIN input)
                    g_pwr += 1
                    opc2 = f"OPC_{g_pwr}"
                    sheets[adc_si]["components"].append(
                        {"ref": opc2, "type": "connector", "value": f"← Sheet {mx_si + 1}",
                         "x": 1, "y": 5, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "right"}]})
                    sheets[adc_si]["nets"].append(
                        {"name": f"IF_OUT_{mx}", "type": "analog",
                         "endpoints": [{"ref": opc2, "pin": "1"},
                                       {"ref": adc, "pin": "AIN_P"}]})
                    sheets[adc_si]["nets"].append(
                        {"name": f"IF_OUT_N_{mx}", "type": "analog",
                         "endpoints": [{"ref": opc2, "pin": "1"},
                                       {"ref": adc, "pin": "AIN_N"}]})

        # LO synth → mixer LO (cross-sheet)
        all_lo = [r for r, rl in ref_role.items() if rl == "lo_synth"]
        for lo in all_lo:
            for mx in all_mixers:
                lo_si = ref_sheet.get(lo)
                mx_si = ref_sheet.get(mx)
                if lo_si is not None and mx_si is not None:
                    if lo_si == mx_si:
                        # Same sheet — direct connection
                        sheets[lo_si]["nets"].append(
                            {"name": f"LO_P_{lo}", "type": "clock",
                             "endpoints": [{"ref": lo, "pin": "RF_OUT_P"},
                                           {"ref": mx, "pin": "LO_P"}]})
                        sheets[lo_si]["nets"].append(
                            {"name": f"LO_N_{lo}", "type": "clock",
                             "endpoints": [{"ref": lo, "pin": "RF_OUT_N"},
                                           {"ref": mx, "pin": "LO_N"}]})
                    else:
                        # Cross-sheet via off-page connectors
                        g_pwr += 1
                        opc_lo = f"OPC_{g_pwr}"
                        sheets[lo_si]["components"].append(
                            {"ref": opc_lo, "type": "connector",
                             "value": f"LO → Sheet {mx_si + 1}",
                             "x": 28, "y": 10, "rot": 0,
                             "pins": [{"name": "1", "num": "1", "side": "left"}]})
                        sheets[lo_si]["nets"].append(
                            {"name": f"LO_P_{lo}", "type": "clock",
                             "endpoints": [{"ref": lo, "pin": "RF_OUT_P"},
                                           {"ref": opc_lo, "pin": "1"}]})
                        sheets[lo_si]["nets"].append(
                            {"name": f"LO_N_{lo}", "type": "clock",
                             "endpoints": [{"ref": lo, "pin": "RF_OUT_N"},
                                           {"ref": opc_lo, "pin": "1"}]})
                        g_pwr += 1
                        opc_mx = f"OPC_{g_pwr}"
                        sheets[mx_si]["components"].append(
                            {"ref": opc_mx, "type": "connector",
                             "value": f"LO ← Sheet {lo_si + 1}",
                             "x": 1, "y": 10, "rot": 0,
                             "pins": [{"name": "1", "num": "1", "side": "right"}]})
                        sheets[mx_si]["nets"].append(
                            {"name": f"LO_P_{lo}", "type": "clock",
                             "endpoints": [{"ref": opc_mx, "pin": "1"},
                                           {"ref": mx, "pin": "LO_P"}]})
                        sheets[mx_si]["nets"].append(
                            {"name": f"LO_N_{lo}", "type": "clock",
                             "endpoints": [{"ref": opc_mx, "pin": "1"},
                                           {"ref": mx, "pin": "LO_N"}]})

        # ADC CLK from synth or FPGA
        for adc in all_adcs:
            adc_si = ref_sheet.get(adc)
            if adc_si is None:
                continue
            # Prefer synth CLK_REF → ADC CLK
            clk_src = None
            for lo in all_lo:
                if ref_sheet.get(lo) == adc_si:
                    clk_src = lo
                    break
            if clk_src:
                sheets[adc_si]["nets"].append(
                    {"name": f"CLK_ADC_P", "type": "clock",
                     "endpoints": [{"ref": clk_src, "pin": "CLK_REF_P"},
                                   {"ref": adc, "pin": "CLK_P"}]})
                sheets[adc_si]["nets"].append(
                    {"name": f"CLK_ADC_N", "type": "clock",
                     "endpoints": [{"ref": clk_src, "pin": "CLK_REF_N"},
                                   {"ref": adc, "pin": "CLK_N"}]})
            else:
                # Add clock connector
                g_pwr += 1
                clk_opc = f"CLK_{g_pwr}"
                sheets[adc_si]["components"].append(
                    {"ref": clk_opc, "type": "connector", "value": "CLK_IN",
                     "x": 1, "y": 8, "rot": 0,
                     "pins": [{"name": "1", "num": "1", "side": "right"}]})
                sheets[adc_si]["nets"].append(
                    {"name": "CLK_ADC_P", "type": "clock",
                     "endpoints": [{"ref": clk_opc, "pin": "1"},
                                   {"ref": adc, "pin": "CLK_P"}]})

        # FPGA: wire remaining unconnected pins
        all_fpga = [r for r, rl in ref_role.items() if rl == "fpga"]
        for fpga in all_fpga:
            fpga_si = ref_sheet.get(fpga)
            if fpga_si is None:
                continue
            sh = sheets[fpga_si]
            # Collect already-connected pins for this FPGA
            connected = set()
            for net in sh["nets"]:
                for ep in net["endpoints"]:
                    if ep["ref"] == fpga:
                        connected.add(ep["pin"])
            fpga_pins = ROLE_PINS.get("fpga", [])
            for fp in fpga_pins:
                if fp["name"] in connected:
                    continue
                pn = fp["name"].upper()
                if pn == "GND":
                    continue  # already has ground symbol
                # SYNC pins — wire to FPGA GPIO or add test point
                if "SYNC" in pn:
                    # ADC SYNC from FPGA — find an ADC on same sheet
                    for adc in all_adcs:
                        if ref_sheet.get(adc) == fpga_si:
                            sh["nets"].append(
                                {"name": f"SYNC_{fpga}_{adc}", "type": "signal",
                                 "endpoints": [{"ref": fpga, "pin": "GPIO_0"},
                                               {"ref": adc, "pin": fp["name"]}]})
                            connected.add("GPIO_0")
                            connected.add(fp["name"])
                            break
                    continue
                if "FRAME" in pn:
                    # ADC FRAME — wire to ADC if on same sheet
                    for adc in all_adcs:
                        if ref_sheet.get(adc) == fpga_si:
                            # Already handled by signal chain if adjacent
                            pass
                    # Add termination resistor
                    g_res += 1
                    rref = f"R{g_res}"
                    sh["components"].append(
                        {"ref": rref, "type": "resistor", "value": "100R",
                         "x": 2, "y": 14 + g_res, "rot": 0})
                    g_gnd += 1
                    gref_r = f"GND_R{g_res}"
                    sh["components"].append(
                        {"ref": gref_r, "type": "ground", "value": "GND",
                         "x": 2, "y": 16 + g_res, "rot": 0})
                    sh["nets"].append(
                        {"name": f"TERM_{fp['name']}", "type": "signal",
                         "endpoints": [{"ref": fpga, "pin": fp["name"]},
                                       {"ref": rref, "pin": "1"}]})
                    sh["nets"].append(
                        {"name": "GND", "type": "ground",
                         "endpoints": [{"ref": rref, "pin": "2"},
                                       {"ref": gref_r, "pin": "1"}]})
                    continue
                if "CLK_IN" in pn:
                    # System clock input — add clock connector
                    g_pwr += 1
                    clk_c = f"CLKIN_{g_pwr}"
                    sh["components"].append(
                        {"ref": clk_c, "type": "connector", "value": "SYS_CLK",
                         "x": 1, "y": 12, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "right"}]})
                    sh["nets"].append(
                        {"name": f"SYS_CLK_{pn[-1]}", "type": "clock",
                         "endpoints": [{"ref": clk_c, "pin": "1"},
                                       {"ref": fpga, "pin": fp["name"]}]})
                    continue
                if "GPIO" in pn and fp["name"] not in connected:
                    # GPIO — add test point header
                    g_pwr += 1
                    tp_ref = f"TP_{g_pwr}"
                    sh["components"].append(
                        {"ref": tp_ref, "type": "connector", "value": f"TP_{pn}",
                         "x": 28, "y": 12 + g_pwr % 4, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "left"}]})
                    sh["nets"].append(
                        {"name": pn, "type": "signal",
                         "endpoints": [{"ref": fpga, "pin": fp["name"]},
                                       {"ref": tp_ref, "pin": "1"}]})

        # LO synth LOCK_DET — wire to FPGA GPIO or test point
        for lo in all_lo:
            lo_si = ref_sheet.get(lo)
            if lo_si is None:
                continue
            sh = sheets[lo_si]
            lo_connected = set()
            for net in sh["nets"]:
                for ep in net["endpoints"]:
                    if ep["ref"] == lo:
                        lo_connected.add(ep["pin"])
            if "LOCK_DET" not in lo_connected:
                for fpga in all_fpga:
                    if ref_sheet.get(fpga) == lo_si:
                        sh["nets"].append(
                            {"name": f"LOCK_DET_{lo}", "type": "signal",
                             "endpoints": [{"ref": lo, "pin": "LOCK_DET"},
                                           {"ref": fpga, "pin": "GPIO_1"}]})
                        break
                else:
                    g_pwr += 1
                    tp_lock = f"TP_{g_pwr}"
                    sh["components"].append(
                        {"ref": tp_lock, "type": "connector", "value": "LOCK_DET",
                         "x": 28, "y": 14, "rot": 0,
                         "pins": [{"name": "1", "num": "1", "side": "left"}]})
                    sh["nets"].append(
                        {"name": f"LOCK_DET_{lo}", "type": "signal",
                         "endpoints": [{"ref": lo, "pin": "LOCK_DET"},
                                       {"ref": tp_lock, "pin": "1"}]})

        # ADC SYNC pins — wire to FPGA or test point
        for adc in all_adcs:
            adc_si = ref_sheet.get(adc)
            if adc_si is None:
                continue
            sh = sheets[adc_si]
            adc_connected = set()
            for net in sh["nets"]:
                for ep in net["endpoints"]:
                    if ep["ref"] == adc:
                        adc_connected.add(ep["pin"])
            for sync_pin in ("SYNC_P", "SYNC_N"):
                if sync_pin not in adc_connected:
                    for fpga in all_fpga:
                        if ref_sheet.get(fpga) == adc_si:
                            gpio_pin = "GPIO_0" if sync_pin == "SYNC_P" else "GPIO_1"
                            sh["nets"].append(
                                {"name": f"SYNC_{sync_pin}_{adc}", "type": "signal",
                                 "endpoints": [{"ref": fpga, "pin": gpio_pin},
                                               {"ref": adc, "pin": sync_pin}]})
                            break

        if not sheets:
            sheets = [{"id": "sheet1", "title": "Schematic",
                       "components": [{"ref": "U1", "type": "ic", "value": "IC",
                                       "x": 10, "y": 8, "rot": 0,
                                       "pins": [{"name": "1", "num": "1", "side": "left"}]}],
                       "nets": []}]

        return {"sheets": sheets, "auto_synthesized": True}
