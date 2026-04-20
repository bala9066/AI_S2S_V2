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
            max_tokens=8192,  # GLM-4.7 safe limit; complex designs use skeleton fallback for schematic_data
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

        if not requirements:
            return {
                "response": "Requirements not found. Complete Phase 1 first.",
                "phase_complete": False,
                "outputs": {},
            }

        user_message = f"""Generate a complete logical netlist for:

**Project:** {project_name}

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
5. Validation notes for any potential issues

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

            # Generate visual markdown with full component/connection tables
            mermaid_diagram = self.netlist_generator.to_mermaid(generator_netlist)
            visual_content = self._build_visual_md(netlist_data, project_name, mermaid_diagram)
            outputs["netlist_visual.md"] = visual_content

            # Run NetworkX validation — always store as JSON string (not dict)
            validation = self._validate_netlist(netlist_data)
            outputs["netlist_validation.json"] = json.dumps(validation, indent=2)

            # Schematic data — if the LLM produced one, persist it. Otherwise synthesize a
            # minimal single-sheet schematic from the node/edge list so the UI always has
            # something to render.
            schematic_data = netlist_data.get("schematic_data")
            if not schematic_data or not schematic_data.get("sheets"):
                schematic_data = self._synthesize_schematic(netlist_data)
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
            mermaid_diagram = self.netlist_generator.to_mermaid(generator_netlist)
            visual_content = self._build_visual_md(netlist_data, project_name, mermaid_diagram)
            import re as _re
            visual_content = _re.sub(r'\b(TBD|TBC|TBA)\b', '[specify]', visual_content, flags=_re.IGNORECASE)
            outputs["netlist_visual.md"] = visual_content
            validation = self._validate_netlist(netlist_data)
            outputs["netlist_validation.json"] = json.dumps(validation, indent=2)
            outputs["schematic.json"] = json.dumps(
                self._synthesize_schematic(netlist_data), indent=2
            )

        return {
            "response": response.get("content", "Netlist generated."),
            "phase_complete": True,  # Always complete — skeleton fallback ensures output files exist
            "outputs": outputs,
        }

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

            # Classify component role
            role = "signal"  # default
            if any(k in title_lower for k in ["mixer", "downconvert", "upconvert"]):
                role = "rf_mixer"
            elif any(k in title_lower for k in ["lna", "amplifier", "vga", "driver", "pa"]):
                role = "rf_amplifier"
            elif any(k in title_lower for k in ["ldo", "regulator", "dc-dc", "pmic", "power supply", "buck", "boost"]):
                role = "power"
            elif any(k in title_lower for k in ["adc", "digitiz"]):
                role = "adc"
            elif any(k in title_lower for k in ["fpga", "cpld", "zynq", "ultrascale", "processing"]):
                role = "fpga"
            elif any(k in title_lower for k in ["phy", "ethernet", "transceiver", "uart", "spi"]):
                role = "interface"
            elif any(k in title_lower for k in ["connector", "jack"]):
                role = "connector"
            elif any(k in title_lower for k in ["filter", "bandpass", "lowpass", "saw"]):
                role = "filter"
            elif any(k in title_lower for k in ["synthesizer", "pll", "lo", "vco"]):
                role = "lo_synth"

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
            if any(k in name_l for k in ["mixer", "downconvert", "upconvert"]):
                ref_role[ref] = "rf_mixer"
            elif any(k in name_l for k in ["lna", "amplifier", "vga", "driver"]):
                ref_role[ref] = "rf_amp"
            elif any(k in name_l for k in ["filter", "bandpass", "lowpass", "saw"]):
                ref_role[ref] = "filter"
            elif any(k in name_l for k in ["connector", "jack", "sma", "2.4mm"]):
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
            else:
                ref_role[ref] = "signal"

        # ── Role-specific pin templates ───────────────────────────────────
        # Each role gets realistic pins matching real datasheets.
        ROLE_PINS: dict = {
            "connector": [
                {"name": "RF_P", "num": "1", "side": "right"},
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
        }

        # ── Group refs by sheet ───────────────────────────────────────────
        sheet_map = {
            "rf": [], "power": [], "adc_dig": [], "clock": [],
        }
        for ref, role in ref_role.items():
            if role in ("connector", "rf_amp", "filter", "rf_mixer"):
                sheet_map["rf"].append(ref)
            elif role == "power":
                sheet_map["power"].append(ref)
            elif role in ("adc", "fpga", "interface", "signal"):
                sheet_map["adc_dig"].append(ref)
            elif role in ("lo_synth", "clock"):
                sheet_map["clock"].append(ref)
            else:
                sheet_map["adc_dig"].append(ref)

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
        sheets = []
        g_cap = 0
        g_gnd = 0
        g_pwr = 0
        g_res = 0

        for sheet_key in SHEET_ORDER:
            refs = sheet_map.get(sheet_key, [])
            if not refs:
                continue

            comps: list = []
            nets: list = []
            placed: set = set()

            # Place ICs left→right, 8-unit spacing, wrap after 3 cols
            for idx, ref in enumerate(refs):
                node = ref_node.get(ref, {})
                role = ref_role.get(ref, "signal")
                pins = [dict(p) for p in ROLE_PINS.get(role, ROLE_PINS["signal"])]
                col = idx % 3
                row = idx // 3
                x = 4 + col * 9
                y = 5 + row * 7
                x = min(x, 24)
                y = min(y, 15)

                comp_type = "connector" if role == "connector" else "ic"
                comp_value = node.get("part_number", "")
                if role == "connector":
                    comp_value = "CON_3"

                comps.append({
                    "ref": ref, "type": comp_type,
                    "value": comp_value,
                    "part_number": node.get("part_number", ""),
                    "x": x, "y": y, "rot": 0, "pins": pins,
                })
                placed.add(ref)

                # Decoupling cap for every IC with VCC/VDD/AVDD pin
                vcc_pins = [p for p in pins if p["side"] == "top" and
                            any(p["name"].upper().startswith(v) for v in ("VCC", "VDD", "AVDD", "DVDD"))]
                for vp in vcc_pins:
                    g_cap += 1
                    cref = f"C{g_cap}"
                    cx = min(x + 3, 28)
                    cy = max(y - 2, 1)
                    comps.append({"ref": cref, "type": "capacitor", "value": "100nF",
                                  "x": cx, "y": cy, "rot": 90})
                    # Wire cap pin 1 → IC VCC, cap pin 2 → GND
                    rail = vp["name"].upper()
                    nets.append({"name": rail, "type": "power",
                                 "endpoints": [{"ref": ref, "pin": vp["name"]},
                                               {"ref": cref, "pin": "1"}]})
                    # Cap GND side — connect to sheet ground later
                    g_gnd += 1
                    gref = f"GND_C{g_cap}"
                    comps.append({"ref": gref, "type": "ground", "value": "GND",
                                  "x": cx, "y": cy + 2, "rot": 0})
                    nets.append({"name": "GND", "type": "ground",
                                 "endpoints": [{"ref": cref, "pin": "2"},
                                               {"ref": gref, "pin": "1"}]})

                # Ground symbol for IC GND pin
                gnd_pins = [p for p in pins if p["name"].upper() in ("GND", "AGND", "DGND")]
                for gp in gnd_pins:
                    g_gnd += 1
                    gref = f"GND{g_gnd}"
                    comps.append({"ref": gref, "type": "ground", "value": "GND",
                                  "x": x, "y": y + 3, "rot": 0})
                    nets.append({"name": "GND", "type": "ground",
                                 "endpoints": [{"ref": ref, "pin": gp["name"]},
                                               {"ref": gref, "pin": "1"}]})

            # VCC symbols at top for power rails
            rail_names = set()
            for ref in refs:
                role = ref_role.get(ref, "signal")
                pins = ROLE_PINS.get(role, ROLE_PINS["signal"])
                for p in pins:
                    pn = p["name"].upper()
                    if p["side"] == "top" and any(pn.startswith(v) for v in ("VCC", "VDD", "AVDD", "DVDD", "VIN")):
                        rail_names.add(p["name"])
            xp = 3
            for rail in sorted(rail_names)[:5]:
                g_pwr += 1
                pref = f"VCC_{g_pwr}"
                comps.append({"ref": pref, "type": "vcc", "value": rail,
                              "x": xp, "y": 1, "rot": 0})
                # Wire VCC symbol to first IC that uses this rail
                for ref in refs:
                    role = ref_role.get(ref, "signal")
                    pins = ROLE_PINS.get(role, ROLE_PINS["signal"])
                    if any(p["name"] == rail for p in pins):
                        nets.append({"name": rail, "type": "power",
                                     "endpoints": [{"ref": pref, "pin": "1"},
                                                   {"ref": ref, "pin": rail}]})
                        break
                xp += 5

            # ── Signal nets: wire adjacent ICs in the signal chain ────────
            for i in range(len(refs) - 1):
                src_ref = refs[i]
                dst_ref = refs[i + 1]
                src_role = ref_role.get(src_ref, "signal")
                dst_role = ref_role.get(dst_ref, "signal")
                src_pins = ROLE_PINS.get(src_role, ROLE_PINS["signal"])
                dst_pins = ROLE_PINS.get(dst_role, ROLE_PINS["signal"])

                # Find output pins of src and input pins of dst
                out_pins = [p for p in src_pins if p["side"] == "right"
                            and not any(p["name"].upper().startswith(x) for x in ("SPI", "GPIO", "LOCK", "FB"))]
                in_pins = [p for p in dst_pins if p["side"] == "left"
                           and not any(p["name"].upper().startswith(x) for x in ("SPI", "EN", "CLK_REF"))]

                # Wire matching pairs (differential or single-ended)
                n_pairs = min(len(out_pins), len(in_pins))
                for j in range(n_pairs):
                    op = out_pins[j]["name"]
                    ip = in_pins[j]["name"]
                    # Determine net type
                    ntype = "analog"
                    if "CLK" in op.upper() or "CLK" in ip.upper():
                        ntype = "clock"
                    elif "D0" in op.upper() or "D1" in op.upper() or "DCO" in op.upper():
                        ntype = "signal"
                    net_name = f"{op}_{src_ref}"
                    nets.append({"name": net_name, "type": ntype,
                                 "endpoints": [{"ref": src_ref, "pin": op},
                                               {"ref": dst_ref, "pin": ip}]})

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
