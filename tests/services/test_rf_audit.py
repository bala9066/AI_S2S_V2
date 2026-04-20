"""Tests for services/rf_audit.py — P0.2 / P1.5 / P1.6 glue.

Network HEAD probes are always stubbed. We only exercise the orchestration
logic + issue production.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.rf_audit import (
    run_all,
    run_banned_parts_audit,
    run_datasheet_audit,
    run_topology_audit,
)


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

def test_topology_audit_emits_issue_for_missing_mixer():
    mermaid = "flowchart TD\n ANT[Antenna] --> LNA[LNA]\n LNA --> ADC[ADC]"
    issues = run_topology_audit(mermaid, architecture="superhet_single")
    assert any(i.severity == "critical" and "mixer" in i.detail.lower() for i in issues)
    for i in issues:
        assert i.category == "topology"
        assert i.location == "block_diagram_mermaid"


def test_topology_audit_passes_clean_superhet():
    mermaid = (
        "flowchart TD\n"
        " ANT[Antenna] --> BPF[Preselector BPF]\n"
        " BPF --> LNA[LNA]\n"
        " LNA --> MIX[Mixer]\n"
        " LO[Synthesizer PLL] --> MIX\n"
        " MIX --> IF[IF Filter]\n"
    )
    issues = run_topology_audit(mermaid, architecture="superhet_single")
    assert issues == []


def test_topology_audit_empty_mermaid():
    issues = run_topology_audit("", architecture="superhet_single")
    assert len(issues) == 1
    assert issues[0].severity == "critical"


# ---------------------------------------------------------------------------
# Datasheet verification
# ---------------------------------------------------------------------------

def test_datasheet_audit_flags_unresolvable_url(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")  # allow network
    with patch("services.rf_audit.verify_url", return_value=False), \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=False):
        issues = run_datasheet_audit([
            {"part_number": "FAKE123", "datasheet_url": "https://bogus.example/fake.pdf"},
        ])
    assert len(issues) == 1
    assert issues[0].severity == "high"
    assert issues[0].category == "datasheet_url"
    assert "FAKE123" in issues[0].detail


def test_datasheet_audit_trusted_vendor_short_circuits(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")
    with patch("services.rf_audit.verify_url", return_value=False), \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=True):
        issues = run_datasheet_audit([
            {"part_number": "ADL8107", "datasheet_url": "https://www.analog.com/..."},
        ])
    # Trusted-vendor URL → no issue even though HEAD would have failed.
    assert issues == []


def test_datasheet_audit_missing_url_flagged_medium(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "")
    issues = run_datasheet_audit([
        {"part_number": "X1"},  # no datasheet_url field
    ])
    assert len(issues) == 1
    assert issues[0].severity == "medium"
    assert "no `datasheet_url`" in issues[0].detail


def test_datasheet_audit_network_disabled_still_accepts_trusted_urls(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    with patch("services.rf_audit.verify_url") as mock_verify, \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=True):
        issues = run_datasheet_audit([
            {"part_number": "X", "datasheet_url": "https://www.ti.com/foo"},
        ])
    mock_verify.assert_not_called()
    assert issues == []


def test_datasheet_audit_network_disabled_flags_untrusted(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    with patch("services.rf_audit.verify_url") as mock_verify, \
         patch("services.rf_audit.is_trusted_vendor_url", return_value=False):
        issues = run_datasheet_audit([
            {"part_number": "X", "datasheet_url": "https://random.blog/x.pdf"},
        ])
    mock_verify.assert_not_called()
    assert len(issues) == 1
    assert "network disabled" in issues[0].detail


# ---------------------------------------------------------------------------
# Banned parts
# ---------------------------------------------------------------------------

def test_banned_parts_audit_returns_cleaned_list_and_issues():
    bom = [
        {"part_number": "HMC8410", "manufacturer": "ADI"},
        {"part_number": "HMC-C024", "manufacturer": "ADI"},
    ]
    cleaned, issues = run_banned_parts_audit(bom)
    assert [c["part_number"] for c in cleaned] == ["HMC8410"]
    assert len(issues) == 1
    assert issues[0].category == "banned_part"


# ---------------------------------------------------------------------------
# run_all orchestrator
# ---------------------------------------------------------------------------

def test_run_all_runs_every_check_and_mutates_bom(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    tool_input = {
        "block_diagram_mermaid": (
            "flowchart TD\n"
            " ANT[Antenna] --> BPF[Preselector BPF]\n"
            " BPF --> LNA[LNA]\n"
            " LNA --> MIX[Mixer]\n"
            " LO[LO] --> MIX\n"
            " MIX --> IF[IF Filter]\n"
        ),
        "component_recommendations": [
            # active part, trusted-vendor URL → pass
            {"part_number": "HMC8410",
             "manufacturer": "Analog Devices",
             "datasheet_url": "https://www.analog.com/en/products/hmc8410.html"},
            # banned — must be stripped
            {"part_number": "HMC-C024",
             "manufacturer": "Analog Devices",
             "datasheet_url": "https://www.analog.com/en/products/hmc-c024.html"},
        ],
    }
    with patch("services.rf_audit.is_trusted_vendor_url", return_value=True):
        new_input, issues = run_all(tool_input, architecture="superhet_single")

    # Banned part removed from the BOM
    parts = [c["part_number"] for c in new_input["component_recommendations"]]
    assert parts == ["HMC8410"]

    # One banned_part issue surfaced
    banned = [i for i in issues if i.category == "banned_part"]
    assert len(banned) == 1


def test_run_all_empty_bom_does_not_raise(monkeypatch):
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    new_input, issues = run_all(
        {"block_diagram_mermaid": ""},
        architecture=None,
    )
    # Empty mermaid still yields the "no nodes" critical topology issue
    assert any(i.category == "topology" for i in issues)


# ---------------------------------------------------------------------------
# Live part validation (DigiKey → Mouser → seed)
# ---------------------------------------------------------------------------

def test_part_validation_flags_hallucinated_mpn(monkeypatch):
    """When DigiKey / Mouser are configured and every tier misses, the
    LLM-invented MPN surfaces as a critical hallucinated_part issue."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from unittest.mock import patch
    with patch("services.rf_audit._distributor_lookup", return_value=None):
        enriched, issues = run_part_validation_audit([
            {"part_number": "HALLUCINATED-XYZ",
             "manufacturer": "Made Up Corp",
             "datasheet_url": "https://fake.example/nope.pdf"},
        ])
    assert any(i.category == "hallucinated_part" for i in issues)
    assert any("HALLUCINATED-XYZ" in i.detail for i in issues)
    assert [c["part_number"] for c in enriched] == ["HALLUCINATED-XYZ"]


def test_part_validation_enriches_when_found(monkeypatch):
    """A DigiKey hit should overwrite the LLM's manufacturer/datasheet
    fields with the authoritative distributor values."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    real = PartInfo(
        part_number="ADL8107", manufacturer="Analog Devices Inc.",
        description="Wideband LNA 2-18 GHz",
        datasheet_url="https://www.analog.com/en/products/adl8107.html",
        product_url="https://www.digikey.com/...",
        lifecycle_status="active", unit_price_usd=24.0,
        stock_quantity=180, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=real):
        enriched, issues = run_part_validation_audit([
            # LLM guessed the manufacturer + datasheet wrong
            {"part_number": "ADL8107", "manufacturer": "ADI Wrong Name",
             "datasheet_url": "https://llm-invented.example/adl8107.pdf"},
        ])
    assert issues == []
    c = enriched[0]
    assert c["manufacturer"] == "Analog Devices Inc."  # overwritten
    assert c["datasheet_url"] == "https://www.analog.com/en/products/adl8107.html"
    assert c["lifecycle_status"] == "active"
    assert c["distributor_source"] == "digikey"


def test_part_validation_flags_nrnd_parts(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    nrnd = PartInfo(
        part_number="OLD-PART", manufacturer="Vendor", description="",
        datasheet_url=None, product_url=None,
        lifecycle_status="nrnd", unit_price_usd=None,
        stock_quantity=None, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=nrnd):
        _, issues = run_part_validation_audit([{"part_number": "OLD-PART"}])
    assert any(i.category == "nrnd_part" and i.severity == "high" for i in issues)


def test_part_validation_flags_obsolete_parts(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    from services.rf_audit import run_part_validation_audit
    from tools.digikey_api import PartInfo
    from unittest.mock import patch
    obs = PartInfo(
        part_number="DEAD", manufacturer="V", description="",
        datasheet_url=None, product_url=None,
        lifecycle_status="obsolete", unit_price_usd=None,
        stock_quantity=None, source="digikey",
    )
    with patch("services.rf_audit._distributor_lookup", return_value=obs):
        _, issues = run_part_validation_audit([{"part_number": "DEAD"}])
    assert any(i.category == "obsolete_part" and i.severity == "critical" for i in issues)


def test_part_validation_skips_when_no_distributor_configured(monkeypatch):
    """Air-gap: no keys set and seed miss → no issues raised (we can't
    distinguish hallucination from "no oracle")."""
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "1")
    from services.rf_audit import run_part_validation_audit
    from unittest.mock import patch
    with patch("services.rf_audit._distributor_lookup", return_value=None):
        _, issues = run_part_validation_audit([{"part_number": "Invented-9"}])
    # Without live configuration we refuse to accuse the LLM of fabrication.
    assert not any(i.category == "hallucinated_part" for i in issues)


def test_run_all_integrates_part_validation(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "x")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "y")
    monkeypatch.setenv("SKIP_DISTRIBUTOR_LOOKUP", "")
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    from unittest.mock import patch
    from services.rf_audit import run_all
    from tools.digikey_api import PartInfo
    tool_input = {
        "block_diagram_mermaid": (
            "flowchart TD\n"
            " ANT[Antenna] --> BPF[Preselector BPF]\n"
            " BPF --> LNA[LNA]\n"
            " LNA --> MIX[Mixer]\n"
            " LO[LO] --> MIX\n"
            " MIX --> IF[IF Filter]\n"
        ),
        "component_recommendations": [
            {"part_number": "ADL8107", "manufacturer": "Wrong",
             "datasheet_url": "https://wrong.example/"},
            {"part_number": "INVENTED-7777", "manufacturer": "LLM Fictions",
             "datasheet_url": "https://invented.example/"},
        ],
    }
    real = PartInfo(
        part_number="ADL8107", manufacturer="Analog Devices",
        description="", datasheet_url="https://www.analog.com/en/products/adl8107.html",
        product_url=None, lifecycle_status="active",
        unit_price_usd=None, stock_quantity=None, source="digikey",
    )
    def _fake(pn, **_k):
        return real if pn == "ADL8107" else None
    with patch("services.rf_audit._distributor_lookup", side_effect=_fake):
        new_input, issues = run_all(tool_input, architecture="superhet_single")

    # ADL8107 kept + enriched; INVENTED-7777 kept but flagged hallucinated.
    parts = {c["part_number"]: c for c in new_input["component_recommendations"]}
    assert parts["ADL8107"]["manufacturer"] == "Analog Devices"
    assert any(
        i.category == "hallucinated_part" and "INVENTED-7777" in i.detail
        for i in issues
    )


def test_run_all_handles_bom_key_alias(monkeypatch):
    """Accept both `component_recommendations` and `bom` as the key."""
    monkeypatch.setenv("SKIP_DATASHEET_VERIFY", "1")
    tool_input = {
        "block_diagram_mermaid": "flowchart TD\n LNA[LNA] --> OUT[Output]",
        "bom": [{"part_number": "HMC-C024", "manufacturer": "ADI"}],
    }
    new_input, issues = run_all(tool_input, architecture="recommend")
    assert new_input["bom"] == []
    assert any(i.category == "banned_part" for i in issues)
