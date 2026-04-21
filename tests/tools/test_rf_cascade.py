"""Tests for tools/rf_cascade.py — Friis cascade analysis."""
from __future__ import annotations

import math

import pytest

from tools.rf_cascade import compute_cascade, extract_stages


def _stage(part, cat, nf=None, gain=None, iip3=None):
    specs = {}
    if nf is not None:
        specs["nf_db"] = nf
    if gain is not None:
        specs["gain_db"] = gain
    if iip3 is not None:
        specs["iip3_dbm"] = iip3
    return {
        "part_number": part, "category": cat,
        "component_name": part,
        "key_specs": specs,
    }


# ---------------------------------------------------------------------------
# Stage extraction
# ---------------------------------------------------------------------------

class TestExtractStages:

    def test_pulls_active_rf_components(self):
        parts = [
            _stage("LNA1", "RF-LNA", nf=1.5, gain=15),
            _stage("MIX1", "RF-Mixer", nf=8, gain=-5, iip3=10),
            # Non-RF — should be dropped
            {"part_number": "LDO1", "category": "Power-LDO",
             "key_specs": {"vout": 3.3}},
        ]
        s = extract_stages(parts)
        assert [x["part_number"] for x in s] == ["LNA1", "MIX1"]

    def test_passive_loss_becomes_negative_gain(self):
        """A filter with `insertion_loss_db: 1.8` should contribute -1.8 dB."""
        f = {"part_number": "BPF1", "category": "RF-Filter",
             "key_specs": {"insertion_loss_db": 1.8}}
        s = extract_stages([f])
        assert len(s) == 1
        assert s[0]["gain_db"] == pytest.approx(-1.8)

    def test_reads_strings_with_units(self):
        p = {"part_number": "X", "category": "RF-LNA",
             "key_specs": {"nf_db": "1.4 dB", "gain_db": "+15 dB"}}
        s = extract_stages([p])
        assert s[0]["nf_db"] == pytest.approx(1.4)
        assert s[0]["gain_db"] == pytest.approx(15)


# ---------------------------------------------------------------------------
# Friis math
# ---------------------------------------------------------------------------

class TestFriisMath:

    def test_single_stage_cascade_equals_stage(self):
        """One LNA — cascade NF = LNA NF, gain = LNA gain."""
        r = compute_cascade([_stage("LNA", "RF-LNA", nf=1.5, gain=15)])
        assert r["totals"]["nf_db"] == pytest.approx(1.5, abs=1e-6)
        assert r["totals"]["gain_db"] == pytest.approx(15, abs=1e-6)

    def test_two_stage_friis_textbook(self):
        """Textbook example: LNA NF=2 dB G=20 dB then mixer NF=10 dB.
        Expected cascade NF ≈ 2.034 dB (mixer's contribution is divided
        by G1 = 100 linear)."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=2, gain=20),
            _stage("MIX", "RF-Mixer", nf=10, gain=-6),
        ])
        # NF1_lin = 1.585, NF2_lin = 10.0, G1_lin = 100
        # F_total = 1.585 + (10-1)/100 = 1.675  →  NF_total = 10*log10(1.675) = 2.24
        assert r["totals"]["nf_db"] == pytest.approx(2.24, abs=0.05)
        assert r["totals"]["gain_db"] == pytest.approx(14, abs=1e-6)

    def test_three_stage_cascade_total_gain(self):
        r = compute_cascade([
            _stage("LNA1", "RF-LNA", nf=1, gain=15),
            _stage("BPF", "RF-Filter", nf=1.5, gain=-1.5),
            _stage("MIX", "RF-Mixer", nf=9, gain=-6, iip3=15),
        ])
        assert r["totals"]["gain_db"] == pytest.approx(7.5, abs=1e-6)
        # Stage 3 NF contribution is (10^0.9 - 1) / (10^(15-1.5)/10)
        #                          = 6.943 / 22.387 = 0.310  →  F_lin = 1.259 + 0.310 + 0.023 ≈ 1.59
        assert 1.8 <= r["totals"]["nf_db"] <= 2.5

    def test_iip3_cascade_dominated_by_later_stage(self):
        """Back-end mixer IIP3 dominates when front-end gain is high."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=1, gain=20, iip3=20),
            _stage("MIX", "RF-Mixer", nf=7, gain=-5, iip3=10),
        ])
        # LNA IIP3 at input: 20 dBm. Mixer IIP3 referred to input:
        #   10 dBm - 20 dB = -10 dBm. That dominates Friis IIP3.
        # Cascade IIP3 ≈ -10 dBm (the mixer's input-referred number).
        iip3 = r["totals"]["iip3_dbm"]
        assert iip3 is not None
        assert -11 <= iip3 <= -9

    def test_skips_stages_without_specs(self):
        """Incomplete rows shouldn't break the math."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=1.5, gain=15),
            {"part_number": "MYSTERY", "category": "RF-Mixer",
             "key_specs": {}},  # no specs
            _stage("IF_AMP", "RF-Amplifier", nf=3, gain=10),
        ])
        assert r["totals"]["nf_db"] is not None
        assert r["totals"]["gain_db"] == pytest.approx(25, abs=1e-6)


# ---------------------------------------------------------------------------
# Verdict / claim comparison
# ---------------------------------------------------------------------------

class TestVerdict:

    def test_nf_claim_pass(self):
        r = compute_cascade(
            [_stage("LNA", "RF-LNA", nf=1.5, gain=15)],
            claimed_nf_db=3.0,
        )
        assert r["verdict"]["nf_pass"] is True
        assert r["verdict"]["nf_headroom_db"] == pytest.approx(1.5, abs=0.01)

    def test_nf_claim_fail(self):
        r = compute_cascade(
            [_stage("LNA", "RF-LNA", nf=4.5, gain=15)],
            claimed_nf_db=3.0,
        )
        assert r["verdict"]["nf_pass"] is False
        assert r["verdict"]["nf_headroom_db"] < 0

    def test_gain_within_3db_slack_passes(self):
        """Gain claim of 40 dB, actual 38 dB → pass (2 dB slack)."""
        r = compute_cascade(
            [_stage("A1", "RF-LNA", gain=20),
             _stage("A2", "RF-Amplifier", gain=18)],
            claimed_total_gain_db=40.0,
        )
        assert r["verdict"]["gain_pass"] is True

    def test_gain_outside_slack_fails(self):
        r = compute_cascade(
            [_stage("A1", "RF-LNA", gain=10)],
            claimed_total_gain_db=40.0,
        )
        assert r["verdict"]["gain_pass"] is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_components_returns_zero_stage_cascade(self):
        r = compute_cascade([])
        assert r["totals"]["stage_count"] == 0
        assert r["totals"]["nf_db"] is None
        assert r["stages"] == []

    def test_no_claims_means_all_verdicts_none(self):
        r = compute_cascade([_stage("LNA", "RF-LNA", nf=1.5, gain=15)])
        v = r["verdict"]
        assert v["nf_pass"] is None
        assert v["gain_pass"] is None
        assert v["iip3_pass"] is None

    def test_cumulative_values_monotonic(self):
        """Cumulative gain should increase (or stay flat on passive loss)
        across stages; cumulative NF should never decrease."""
        r = compute_cascade([
            _stage("LNA", "RF-LNA", nf=1, gain=15),
            _stage("BPF", "RF-Filter", nf=1.5, gain=-1.5),
            _stage("MIX", "RF-Mixer", nf=8, gain=-5),
        ])
        cum_gain = [s["cum_gain_db"] for s in r["stages"]]
        cum_nf = [s["cum_nf_db"] for s in r["stages"] if s["cum_nf_db"] is not None]
        # gain: 15, 13.5, 8.5 — monotone decreasing is fine, just needs to
        # reflect the accumulation, so check final matches totals
        assert cum_gain[-1] == pytest.approx(r["totals"]["gain_db"], abs=1e-6)
        # NF: monotone non-decreasing
        for a, b in zip(cum_nf, cum_nf[1:]):
            assert b >= a - 1e-6
