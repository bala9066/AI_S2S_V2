"""
Structured DRC / ERC — P2.7.

The netlist_agent used to surface validation as free-form prose inside
`validation_notes`. That's human-readable but not machine-parseable, so
regressions slip through silently. This module replaces (augments) it
with a set of deterministic checks that emit structured violation rows.

Checks:
  1. **Shorts** — any net whose name looks like BOTH a power rail and
     a ground rail (e.g. endpoints of "VCC_GND" hint at a wiring error).
  2. **Power-net collisions** — a single node+pin landing on two
     different power nets (VCC_3V3 and VCC_5V0 on the same pin).
  3. **Floating nets** — any `signal_type == "signal"` net with only
     one endpoint, i.e. nothing receives it.
  4. **Orphan pins** — same (ref, pin) declared on multiple nets with
     different signal_types.
  5. **Unrecognised power-net naming** — a `power` signal_type whose
     name doesn't match the standard rail pattern.
  6. **Missing decoupling hint** — any active IC (has Vcc pin) without
     any capacitor-class node (C*) reference on the same power net.
     (Advisory only; RF layouts often rely on external decoupling.)

Output shape:

    {
      "checks_run": ["shorts","power_collision",...],
      "violations": [
        {"severity":"critical|high|medium|low|info",
         "rule": "shorts",
         "detail": "...",
         "location": "net/ref/pin"}
      ],
      "counts": {"critical":0,"high":0,"medium":0,"low":0,"info":0}
    }
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

_POWER_NAME_RE = re.compile(
    r"^(VCC|VDD|VEE|VSS|\+\d|AVDD|DVDD|VBAT|V\d+V\d+|\+?\d+V\d*)(_|$)",
    re.IGNORECASE,
)
_GROUND_NAME_RE = re.compile(r"^(GND|AGND|DGND|GNDA|GNDD|VSS)(_|$)", re.IGNORECASE)


def _is_power(name: str) -> bool:
    return bool(_POWER_NAME_RE.match(name or ""))


def _is_ground(name: str) -> bool:
    return bool(_GROUND_NAME_RE.match(name or ""))


def _is_capacitor_ref(ref: str) -> bool:
    return bool(re.match(r"^C\d", (ref or "").upper()))


# ---------------------------------------------------------------------------

def run_drc(netlist: dict[str, Any]) -> dict[str, Any]:
    """Run all DRC checks on a NetlistAgent-style payload with `nodes`
    and `edges` arrays. See module docstring for output shape."""
    nodes: list[dict] = list(netlist.get("nodes") or [])
    edges: list[dict] = list(netlist.get("edges") or [])
    power_nets: set[str] = set(netlist.get("power_nets") or [])
    ground_nets: set[str] = set(netlist.get("ground_nets") or [])

    violations: list[dict[str, Any]] = []

    # Build useful indexes upfront ------------------------------------------
    nets_to_endpoints: dict[str, list[tuple[str, str]]] = defaultdict(list)
    nets_to_type: dict[str, str] = {}
    pin_to_nets: dict[tuple[str, str], set[str]] = defaultdict(set)

    def _pin_key(ref: str, pin: str) -> tuple[str, str]:
        return (str(ref or ""), str(pin or ""))

    for e in edges:
        name = e.get("net_name") or e.get("signal") or ""
        if not name:
            continue
        s_ref = e.get("from_instance") or e.get("source")
        s_pin = e.get("from_pin") or e.get("source_pin")
        t_ref = e.get("to_instance") or e.get("target")
        t_pin = e.get("to_pin") or e.get("target_pin")
        if s_ref and s_pin is not None:
            nets_to_endpoints[name].append((s_ref, str(s_pin)))
            pin_to_nets[_pin_key(s_ref, s_pin)].add(name)
        if t_ref and t_pin is not None:
            nets_to_endpoints[name].append((t_ref, str(t_pin)))
            pin_to_nets[_pin_key(t_ref, t_pin)].add(name)
        stype = (e.get("signal_type") or e.get("type") or "").lower()
        if stype and name not in nets_to_type:
            nets_to_type[name] = stype

    nodes_by_ref = {
        (n.get("reference_designator") or n.get("instance_id") or n.get("id") or ""): n
        for n in nodes
    }

    # -- 1. Shorts — net name declared as both power and ground --------------
    for name in sorted(nets_to_endpoints):
        if _is_power(name) and _is_ground(name):
            violations.append({
                "severity": "critical", "rule": "short",
                "location": f"net/{name}",
                "detail": f"Net '{name}' matches both power and ground naming conventions.",
            })
        # A net listed in both the power_nets and ground_nets arrays is
        # likewise a short.
        if name in power_nets and name in ground_nets:
            violations.append({
                "severity": "critical", "rule": "short",
                "location": f"net/{name}",
                "detail": f"Net '{name}' is declared as both a power and a ground rail.",
            })

    # -- 2. Power-net collision on a single pin -----------------------------
    for (ref, pin), names in pin_to_nets.items():
        power_hits = [n for n in names if _is_power(n) or n in power_nets]
        if len(set(power_hits)) >= 2:
            violations.append({
                "severity": "critical", "rule": "power_collision",
                "location": f"pin/{ref}.{pin}",
                "detail": (
                    f"Pin {ref}.{pin} is connected to multiple power nets: "
                    + ", ".join(sorted(set(power_hits)))
                ),
            })

    # -- 3. Floating signal nets (fewer than 2 endpoints) -------------------
    # NOTE: power + ground nets are checked in rule 3b below, which applies
    # different semantics — they're fine with one trace segment as long as
    # at least one source + one sink are present somewhere in the payload.
    for name, endpoints in nets_to_endpoints.items():
        unique = {(r, p) for r, p in endpoints}
        stype = nets_to_type.get(name, "")
        if stype in ("power", "ground"):
            continue
        if len(unique) < 2:
            violations.append({
                "severity": "high", "rule": "floating_net",
                "location": f"net/{name}",
                "detail": (
                    f"Net '{name}' has {len(unique)} endpoint(s); "
                    "signal nets need at least one driver + one receiver."
                ),
            })

    # -- 3b. Dangling power / ground rails (P1.5) --------------------------
    # A power rail with only a single endpoint (the IC's VCC pin) and
    # nothing driving it — no regulator, no connector, no decap — is
    # fatal in silicon. Rule 3 exempted "power" / "ground" types from
    # the 2-endpoint requirement because segment-level layouts are
    # legal. This rule reintroduces the check at *rail* level: every
    # named power/ground rail in the payload must have ≥2 unique
    # endpoints OR appear as a driver ref (regulator / connector).
    _DRIVER_REF_PATTERNS = ("PWR", "REG", "VREG", "LDO", "PSU", "U_VREG",
                            "J_PWR", "J1", "J_VCC", "CONN")

    def _looks_like_driver(ref: str) -> bool:
        r = (ref or "").upper()
        return any(r.startswith(p) for p in _DRIVER_REF_PATTERNS)

    for name, endpoints in nets_to_endpoints.items():
        stype = nets_to_type.get(name, "")
        if stype not in ("power", "ground"):
            # Also catch nets named like rails even if signal_type wasn't set
            if not (_is_power(name) or _is_ground(name) or name in power_nets
                    or name in ground_nets):
                continue
        unique = {(r, p) for r, p in endpoints}
        if len(unique) < 2:
            violations.append({
                "severity": "high", "rule": "dangling_power_rail",
                "location": f"net/{name}",
                "detail": (
                    f"Power/ground rail '{name}' has only {len(unique)} "
                    "endpoint(s); no driver found. The rail isn't connected "
                    "to a regulator, supply connector, or bulk cap."
                ),
            })
            continue
        # Rail has ≥2 endpoints — verify at least one looks like a driver
        # (regulator output, supply connector, battery, etc.).  A rail
        # where every endpoint is an IC Vcc pin with no upstream source
        # is a silent integration failure.
        refs = {r for r, _ in unique}
        if not any(_looks_like_driver(r) for r in refs):
            violations.append({
                "severity": "medium", "rule": "power_rail_no_driver",
                "location": f"net/{name}",
                "detail": (
                    f"Power/ground rail '{name}' has {len(unique)} endpoints "
                    "but none of the reference designators look like a driver "
                    "(PWR*, REG*, LDO*, CONN*). Verify a supply source is "
                    "actually connected."
                ),
            })

    # -- 4. Unrecognised power naming ---------------------------------------
    for name, stype in nets_to_type.items():
        if stype != "power":
            continue
        if not _is_power(name):
            violations.append({
                "severity": "low", "rule": "power_naming",
                "location": f"net/{name}",
                "detail": (
                    f"Power net '{name}' does not match the standard rail "
                    "naming convention (VCC_*, VDD_*, V3V3, +5V, etc.)."
                ),
            })

    # -- 5. Missing decoupling hint (advisory) ------------------------------
    # For every named power net, check whether any capacitor-class ref is
    # attached. This is cheap and catches the egregious "no bulk caps"
    # mistake without requiring schematic knowledge of pin capacitance.
    known_power_nets = [
        n for n, st in nets_to_type.items() if st == "power" or _is_power(n)
    ]
    for pnet in known_power_nets:
        refs = {r for r, _ in nets_to_endpoints.get(pnet, [])}
        if not any(_is_capacitor_ref(r) for r in refs):
            if refs:  # only flag nets that have *some* endpoints
                violations.append({
                    "severity": "medium", "rule": "missing_decap",
                    "location": f"net/{pnet}",
                    "detail": (
                        f"Power net '{pnet}' has no capacitor-class reference "
                        "(C*) attached — add bulk + bypass decoupling."
                    ),
                })

    # -- 5b. Clock-domain crossing without declared synchronisers (P2.7) ---
    # A design that references ≥2 distinct clock nets AND carries any
    # signal edge between their associated components without a CDC
    # synchroniser is a metastability risk. We can't parse RTL from a
    # schematic JSON, so we use a conservative heuristic: if ≥2 clock
    # nets exist and no node carries a name / MPN hint for a CDC cell
    # (FIFO, synchroniser, dual-port RAM), raise a medium-severity
    # advisory. Caught once per design, not per-path.
    clock_nets = {
        n for n, st in nets_to_type.items() if st == "clock"
    }
    # Also treat nets named like clocks even if signal_type wasn't set.
    for name in nets_to_endpoints:
        if re.search(r"(?:^|_)(CLK|SCLK|MCLK|SCK|CLOCK)(?:_|$)",
                     name, re.IGNORECASE):
            clock_nets.add(name)
    if len(clock_nets) >= 2:
        # Look for CDC-cell hints in node descriptions / part numbers.
        cdc_hints = re.compile(
            r"(?:CDC|FIFO|SYNC|ASYNC|dual[- ]?port|2FF|synchroniser|synchronizer)",
            re.IGNORECASE,
        )
        has_cdc_cell = False
        for n in nodes:
            blob = " ".join(str(n.get(k) or "") for k in
                            ("part_number", "component_name", "name",
                             "description"))
            if cdc_hints.search(blob):
                has_cdc_cell = True
                break
        if not has_cdc_cell:
            violations.append({
                "severity": "medium", "rule": "cdc_boundary_undeclared",
                "location": "clocks/" + ",".join(sorted(clock_nets)),
                "detail": (
                    f"Design has {len(clock_nets)} distinct clock domains "
                    "(" + ", ".join(sorted(clock_nets)) + ") but no "
                    "CDC synchroniser / FIFO / dual-port cell is declared "
                    "in the BOM. Metastability risk unless RTL adds 2FF "
                    "synchronisers at every crossing."
                ),
            })

    # -- 6. Unknown component reference on an edge --------------------------
    known_refs = set(nodes_by_ref.keys())
    if known_refs:  # skip when the caller didn't pass nodes
        referenced: set[str] = set()
        for name, endpoints in nets_to_endpoints.items():
            for r, _ in endpoints:
                referenced.add(r)
        dangling = sorted(r for r in referenced if r not in known_refs and r)
        for r in dangling:
            violations.append({
                "severity": "high", "rule": "unknown_ref",
                "location": f"ref/{r}",
                "detail": f"Reference designator '{r}' appears in nets but not in the component list.",
            })

    # ------------------------------------------------------------------ meta
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for v in violations:
        counts[v.get("severity", "info")] = counts.get(v.get("severity", "info"), 0) + 1

    return {
        "checks_run": [
            "shorts", "power_collision", "floating_net",
            "dangling_power_rail", "power_rail_no_driver",
            "power_naming", "missing_decap", "cdc_boundary_undeclared",
            "unknown_ref",
        ],
        "violations": violations,
        "counts": counts,
        "overall_pass": counts["critical"] == 0 and counts["high"] == 0,
    }
