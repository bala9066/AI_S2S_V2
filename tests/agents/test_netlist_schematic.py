"""Tests for the schematic-generation logic in agents.netlist_agent.

Focuses on the bug classes the user called out:
  * RF passives end up on the correct sheet
  * Passive RF blocks don't get VCC pins or decoupling caps
  * Active ICs get the full multi-value decoupling stack
  * Splitter unused ports are terminated into 50 Ω
  * Bias-tee DC_IN gets a choke + bulk cap
  * Components don't overlap geometrically
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agents.netlist_agent import NetlistAgent


def _make_agent() -> NetlistAgent:
    """Bypass __init__ so tests don't need LLM clients."""
    return NetlistAgent.__new__(NetlistAgent)


def _run_schematic(agent: NetlistAgent, nodes: list[dict], edges: list[dict] | None = None):
    """Call the internal schematic builder. Returns the schematic dict."""
    netlist = {
        "nodes": nodes,
        "edges": edges or [],
        "power_nets": ["VCC_5V", "VCC_3V3"],
        "ground_nets": ["GND"],
    }
    return agent._synthesize_schematic(netlist)


def _sheet_of(schematic: dict, ref: str) -> str | None:
    for sh in schematic.get("sheets", []):
        for c in sh.get("components", []):
            if c.get("ref") == ref:
                return sh.get("title", "")
    return None


def _comp(schematic: dict, ref: str) -> dict | None:
    for sh in schematic.get("sheets", []):
        for c in sh.get("components", []):
            if c.get("ref") == ref:
                return c
    return None


def _all_comps(schematic: dict) -> list[dict]:
    out = []
    for sh in schematic.get("sheets", []):
        out.extend(sh.get("components", []))
    return out


def _nets(schematic: dict) -> list[dict]:
    out = []
    for sh in schematic.get("sheets", []):
        out.extend(sh.get("nets", []))
    return out


# --- Sheet-assignment tests -------------------------------------------------

def test_limiter_goes_to_rf_sheet_not_adc():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "PIN Diode Limiter", "part_number": "CLA4603-000"},
    ])
    title = _sheet_of(s, "U1")
    assert title is not None
    assert "RF" in title or "Front" in title, \
        f"Limiter must be on RF sheet, got '{title}'"
    assert "Digit" not in title, \
        f"Limiter must NOT be on ADC sheet, got '{title}'"


def test_bias_tee_goes_to_rf_sheet_not_adc():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Bias-Tee DC Injection", "part_number": "PE1604"},
    ])
    title = _sheet_of(s, "U1")
    assert "RF" in title or "Front" in title, \
        f"Bias-tee must be on RF sheet, got '{title}'"


def test_splitter_goes_to_rf_sheet_not_adc():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "4-Way Wilkinson Splitter", "part_number": "MPD4-0108CSP2"},
    ])
    title = _sheet_of(s, "U1")
    assert "RF" in title or "Front" in title, \
        f"Splitter must be on RF sheet, got '{title}'"


def test_attenuator_and_isolator_on_rf_sheet():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Fixed Attenuator Pad", "part_number": "YAT-6+"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "Isolator", "part_number": "ABC-ISO"},
    ])
    for ref in ("U1", "U2"):
        t = _sheet_of(s, ref)
        assert t and ("RF" in t or "Front" in t), f"{ref} on wrong sheet: {t}"


def test_real_adc_still_goes_to_adc_sheet():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "JESD204B ADC", "part_number": "AD9625"},
    ])
    t = _sheet_of(s, "U1")
    assert t and "Digit" in t, f"ADC must be on ADC/Digitisation sheet, got '{t}'"


# --- Pin-model tests --------------------------------------------------------

def test_passive_limiter_has_no_vcc_pin():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "RF Limiter", "part_number": "CLA4603"},
    ])
    c = _comp(s, "U1")
    assert c is not None
    pin_names = {p["name"].upper() for p in c.get("pins", [])}
    assert "VCC" not in pin_names and "VDD" not in pin_names, \
        f"Limiter must not have VCC/VDD pin, got {pin_names}"
    assert {"RF_IN", "RF_OUT", "GND"}.issubset(pin_names), \
        f"Limiter missing RF ports: {pin_names}"


def test_bias_tee_has_dc_in_no_vcc():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Bias-Tee", "part_number": "PE1604"},
    ])
    c = _comp(s, "U1")
    assert c is not None
    pin_names = {p["name"].upper() for p in c.get("pins", [])}
    assert "VCC" not in pin_names, f"bias_tee should not have VCC: {pin_names}"
    assert "DC_IN" in pin_names, f"bias_tee missing DC_IN port: {pin_names}"
    assert "RF_IN" in pin_names and "RF_OUT" in pin_names


def test_splitter_has_four_outputs_no_vcc():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Wilkinson Splitter 1:4", "part_number": "BP4U1+"},
    ])
    c = _comp(s, "U1")
    assert c is not None
    pin_names = {p["name"].upper() for p in c.get("pins", [])}
    assert "VCC" not in pin_names
    # All four output ports present
    for i in range(1, 5):
        assert f"RF_OUT_{i}" in pin_names, f"missing RF_OUT_{i}"


# --- Decoupling / passive-network tests -------------------------------------

def test_passive_limiter_gets_no_decoupling_cap():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Limiter", "part_number": "CLA4603"},
    ])
    # All caps on the resulting schematic
    all_caps = [c for c in _all_comps(s) if c.get("type") == "capacitor"]
    # A lone limiter sheet should have zero caps (no ESD here because
    # there's no connector upstream, and limiters have no VCC).
    assert len(all_caps) == 0, \
        f"Limiter sheet should have no caps; got {[c['value'] for c in all_caps]}"


def test_active_lna_gets_multi_value_decoupling_stack():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Low-Noise Amplifier", "part_number": "HMC8410"},
    ])
    caps = [c for c in _all_comps(s) if c.get("type") == "capacitor"]
    values = {c["value"] for c in caps}
    # Multi-value stack: bulk + mid + HF
    assert "1uF" in values, f"missing bulk cap, got {values}"
    assert "100nF" in values, f"missing mid-band cap, got {values}"
    assert "10nF" in values, f"missing HF cap, got {values}"


# --- Splitter termination + bias-tee DC feed tests --------------------------

def test_splitter_unused_ports_terminated_with_50ohm():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Wilkinson Splitter", "part_number": "BP4U1+"},
    ])
    resistors = [c for c in _all_comps(s) if c.get("type") == "resistor"]
    # Secondary outputs (RF_OUT_2, RF_OUT_3, RF_OUT_4) all get 50 Ω terminations
    term_res = [r for r in resistors if r.get("value", "") == "50R"]
    assert len(term_res) >= 3, \
        f"expected ≥3 50Ω terminators, got {[r.get('value') for r in resistors]}"
    # Each secondary port should have a net tying it to a resistor
    term_nets = [n for n in _nets(s) if n.get("name", "").startswith("TERM_")]
    assert len(term_nets) >= 3


def test_bias_tee_dc_in_has_choke_and_bulk_cap():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Bias-Tee", "part_number": "PE1604"},
    ])
    inductors = [c for c in _all_comps(s) if c.get("type") == "inductor"]
    bulk_caps = [c for c in _all_comps(s)
                 if c.get("type") == "capacitor" and c.get("value") == "10uF"]
    assert inductors, "bias-tee must get an RF choke on DC_IN"
    assert bulk_caps, "bias-tee must get a bulk cap on DC_IN"


# --- Geometry tests ---------------------------------------------------------

def test_components_do_not_overlap_geometrically():
    agent = _make_agent()
    # Enough components to trigger row wrapping
    nodes = [{"instance_id": f"U{i}", "reference_designator": f"U{i}",
              "component_name": "LNA", "part_number": f"HMC841{i}"}
             for i in range(1, 8)]
    s = _run_schematic(agent, nodes)
    ic_positions = [(c["x"], c["y"]) for c in _all_comps(s)
                    if c.get("ref", "").startswith("U")]
    # Two ICs never placed at the same coordinate
    assert len(set(ic_positions)) == len(ic_positions), \
        f"overlap detected: {ic_positions}"
    # Column pitch is at least 8 units so IC bodies don't collide
    xs = sorted({x for x, _ in ic_positions})
    if len(xs) > 1:
        min_gap = min(b - a for a, b in zip(xs, xs[1:]))
        assert min_gap >= 8, f"columns too tight: gap={min_gap}, xs={xs}"


def test_chip_resistor_renders_as_resistor_not_ic():
    """User complaint: the Vishay CRCW chip resistor was drawn as an IC
    block with VCC/GND/IN/OUT pins. Verify it's now a native 2-pin
    resistor symbol."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "R1", "reference_designator": "R1",
         "component_name": "Chip Resistor", "part_number": "CRCW060310K0FKEA"},
    ])
    c = _comp(s, "R1")
    assert c is not None
    assert c["type"] == "resistor", \
        f"resistor must render as type='resistor', got {c['type']}"
    # Passives have no 'pins' list — they use the 2-pin ("1"/"2") convention
    assert "pins" not in c or not c.get("pins")


def test_chip_capacitor_renders_as_capacitor_not_ic():
    """Same failure mode as resistors — Murata GRM part number must map
    to the native capacitor symbol.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "C4", "reference_designator": "C4",
         "component_name": "Chip Capacitor 0.1uF", "part_number": "GRM188R71H104KA93D"},
    ])
    c = _comp(s, "C4")
    assert c is not None
    assert c["type"] == "capacitor", \
        f"capacitor must render as type='capacitor', got {c['type']}"


def test_passive_rlc_gets_no_decoupling_or_vcc():
    """Resistors/caps/inductors/diodes must never get auto-injected
    decoupling stacks or VCC symbols — only real active ICs do.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "R1", "reference_designator": "R1",
         "component_name": "Chip Resistor 10k", "part_number": "CRCW060310K0FKEA"},
        {"instance_id": "C1", "reference_designator": "C1",
         "component_name": "Chip Capacitor 100nF", "part_number": "GRM188R71H104KA93D"},
    ])
    # No VCC symbol should be emitted for an R-and-C-only sheet
    vcc_syms = [c for c in _all_comps(s) if c.get("type") == "vcc"]
    assert vcc_syms == [], \
        f"no VCC symbol should be emitted for passive-only sheet, got {vcc_syms}"
    # Total caps should be exactly 1 (the C1 itself), no decoupling stack
    caps = [c for c in _all_comps(s) if c.get("type") == "capacitor"]
    assert len(caps) == 1, \
        f"expected only C1, got {[c['ref'] for c in caps]}"


def test_auto_ref_counters_do_not_collide_with_llm_refs():
    """User complaint: LLM R1 collided with auto-generated R1 from
    splitter terminators. Verify seed counters skip past existing refs.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "R1", "reference_designator": "R1",
         "component_name": "Chip Resistor", "part_number": "CRCW060310K0FKEA"},
        {"instance_id": "R2", "reference_designator": "R2",
         "component_name": "Chip Resistor", "part_number": "CRCW060310K0FKEA"},
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Wilkinson Splitter 1:4", "part_number": "BP4U1+"},
    ])
    refs = [c["ref"] for c in _all_comps(s) if c.get("ref")]
    # No duplicates
    assert len(refs) == len(set(refs)), \
        f"duplicate refs: {[r for r in refs if refs.count(r) > 1]}"
    # The LLM's R1 and R2 are preserved
    assert "R1" in refs and "R2" in refs
    # Auto-generated splitter terminator resistors come after R2
    term_rs = [r for r in refs if r.startswith("R") and r != "R1" and r != "R2"]
    for r in term_rs:
        suffix = int(r[1:])
        assert suffix >= 3, f"auto-resistor {r} collides with LLM range"


def test_ground_symbol_lands_on_ic_gnd_pin_not_offset():
    """User complaint: U1's ground pin is far from the ground symbol.
    Verify the ground symbol's anchor (0.5, 0) lands on the IC's GND
    pin after pin-anchor math.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Limiter", "part_number": "PE8022"},
    ])
    ic = _comp(s, "U1")
    gnd_syms = [c for c in _all_comps(s) if c.get("type") == "ground"]
    assert gnd_syms, "no ground symbol emitted"

    # For a 3-pin limiter (2 LR pins + 1 bottom GND), size is w=max(4,0+2)=4,
    # h=max(3, max(1,1,2)+1)=3. GND pin lives on bottom: pos = w/(1+1) = 2.
    # So GND pin is at (ic.x + 2, ic.y + 3). The ground symbol's anchor
    # (0.5, 0) must land there → ground.x = ic.x + 1.5, ground.y = ic.y + 3.
    expected_anchor_x = ic["x"] + 2
    expected_anchor_y = ic["y"] + 3

    # The ground symbol directly under the IC GND pin
    ic_gnd_sym = min(gnd_syms,
                     key=lambda g: abs((g["x"] + 0.5) - expected_anchor_x)
                                 + abs(g["y"] - expected_anchor_y))
    assert abs((ic_gnd_sym["x"] + 0.5) - expected_anchor_x) <= 1.0, \
        f"ground x off: ground.x={ic_gnd_sym['x']}, expected anchor x={expected_anchor_x}"
    assert abs(ic_gnd_sym["y"] - expected_anchor_y) <= 1.0, \
        f"ground y off: ground.y={ic_gnd_sym['y']}, expected anchor y={expected_anchor_y}"


def test_esd_diode_is_wired_not_floating():
    """User complaint: D4 (ESD) is hanging. Verify the diode is
    type='diode_tvs', rot=90, and both its pins are in nets."""
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "J1", "reference_designator": "J1",
         "component_name": "SMA Connector", "part_number": "SMA-J"},
    ])
    tvs = [c for c in _all_comps(s) if c.get("type") == "diode_tvs"]
    assert tvs, "no ESD/TVS diode emitted"
    d = tvs[0]
    assert d.get("rot") == 90, \
        f"ESD must be vertical (rot=90) so anode→signal, cathode→GND; got rot={d.get('rot')}"

    # Both pins of the diode must be in nets (not hanging)
    nets = _nets(s)
    d_refs_pins = set()
    for n in nets:
        for ep in n["endpoints"]:
            if ep["ref"] == d["ref"]:
                d_refs_pins.add(ep["pin"])
    assert "1" in d_refs_pins, f"ESD anode (pin 1) not in any net"
    assert "2" in d_refs_pins, f"ESD cathode (pin 2) not in any net"


def test_connector_pin_names_resolve_not_hanging():
    """User complaint: pin 1 of J1 is hanging. Root cause was the
    connector was rendered as type='connector' (numeric pins only) but
    nets referenced named pins like 'RF_P'. Verify the connector is now
    an IC with resolvable named pins.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "J1", "reference_designator": "J1",
         "component_name": "SMA Connector", "part_number": "SMA-J"},
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "LNA", "part_number": "HMC8410"},
    ])
    j = _comp(s, "J1")
    assert j is not None
    assert j.get("type") == "ic", \
        f"connector must render as IC for named-pin lookup; got {j.get('type')}"
    # Pins array present with RF_OUT + GND
    pin_names = {p["name"].upper() for p in j.get("pins", [])}
    assert "RF_OUT" in pin_names, f"connector missing RF_OUT pin: {pin_names}"
    assert "GND" in pin_names, f"connector missing GND pin: {pin_names}"


def test_decoupling_caps_align_to_vcc_pin():
    """User complaint: caps hang near VCC. Verify every decoupling cap's
    pin 1 (at cap.x - 0.5 after rot=90) lands on the IC's VCC anchor x.
    """
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "LNA", "part_number": "HMC8410"},
    ])
    ic = _comp(s, "U1")
    pins = ic.get("pins", [])
    # Compute IC bbox w/h the same way the TS renderer does
    sides = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    for p in pins:
        sides[p["side"]] += 1
    w = max(4, max(sides["top"], sides["bottom"], 0) + 2)
    # Find VCC pin's global x (expecting single top pin centered)
    top_pins = [p for p in pins if p["side"] == "top"]
    vcc_pos_x = ic["x"] + (w / (len(top_pins) + 1)) * 1
    # Only the decoupling-stack caps (rot=90, values 1uF/100nF/10nF) —
    # AC-ground caps on unused differential inputs have rot=0 and a
    # different placement, so we exclude them.
    decoup_caps = [c for c in _all_comps(s)
                   if c.get("type") == "capacitor"
                   and c.get("rot") == 90
                   and c.get("value") in ("1uF", "100nF", "10nF")]
    assert decoup_caps, "no decoupling stack caps found"
    for c in decoup_caps:
        pin1_x = c["x"] - 0.5
        assert abs(pin1_x - vcc_pos_x) <= 1.0, \
            f"cap {c['ref']} pin1 x={pin1_x} does not align with VCC anchor x={vcc_pos_x}"


def test_no_vcc_symbol_emitted_when_sheet_is_all_passive():
    agent = _make_agent()
    s = _run_schematic(agent, [
        {"instance_id": "U1", "reference_designator": "U1",
         "component_name": "Limiter", "part_number": "CLA4603"},
        {"instance_id": "U2", "reference_designator": "U2",
         "component_name": "Wilkinson Splitter", "part_number": "MPD4-0108CSP2"},
    ])
    vcc_syms = [c for c in _all_comps(s) if c.get("type") == "vcc"]
    assert vcc_syms == [], \
        f"all-passive sheet must emit no VCC symbol, got {vcc_syms}"
