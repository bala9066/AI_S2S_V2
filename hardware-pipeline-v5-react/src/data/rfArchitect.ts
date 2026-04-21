/**
 * v21 — RF Architect data + pure-function helpers.
 *
 * All flow control for P1 Round-1 elicitation lives client-side now: this
 * module owns the seven-stage wizard's data and the deterministic architect
 * intelligence (Friis-cascade derivation, auto-suggestions, cascade sanity
 * rules). Ported from v21-prototype.html.
 *
 * Bugs A-D fixed during the port (see IMPLEMENTATION_PLAN.md v21 section):
 *   A. adc_enob chips normalised to -bit suffix in all scopes
 *   B. bw_vs_adc uses Hz normalisation
 *   C. radar_arch_fit guarded to downconversion/full scope
 *   D. freq_plan_image reads if1_freq fallback for superhet_double
 */

import type { DesignScope } from '../types';

/* ================================================================
   STAGE 1 — PROJECT TYPE (kept for future transmitter / power-supply
   flows; not rendered in current React port — only receiver is wired).
   ================================================================ */
export interface ProjectTypeDef {
  id: string;
  name: string;
  desc: string;
  examples: string;
  supported: boolean;
}

export const PROJECT_TYPES: Record<string, ProjectTypeDef> = {
  receiver:     { id: 'receiver',     name: 'Receiver',     desc: 'Antenna → signal capture + conditioning + (optional) digitisation.',   examples: 'Receiver 5-18 GHz wideband · X-band radar RX · Ku-band SATCOM downconverter', supported: true },
  transmitter:  { id: 'transmitter',  name: 'Transmitter',  desc: 'Signal generation + amplification + spectral cleanup.',                examples: 'Transmitter 2-8 GHz PA chain · S-band radar TX · Ku-band uplink',             supported: true },
  transceiver:  { id: 'transceiver',  name: 'Transceiver',  desc: 'Combined TX + RX — shared LO / antenna.',                              examples: 'SDR TRX 70 MHz-6 GHz · 5G NR front-end · Half-duplex comms link',             supported: false },
  power_supply: { id: 'power_supply', name: 'Power Supply', desc: 'DC-DC conversion — buck / boost / LLC / flyback topology.',            examples: 'DC-DC 24V → 5V, 10A · Dual-rail ±12V / 3A · PoE-PD 30W',                      supported: false },
};

/* ================================================================
   STAGE 1 — SCOPE (in React the wizard starts here; Stage 0 TYPE is
   implicit — "receiver" — because the only wired flow is receiver).
   ================================================================ */
export const SCOPE_DESC: Record<DesignScope, { desc: string; covers: string }> = {
  'full':           { desc: 'Antenna → DSP. Every phase runs (P1 through P8c).',                  covers: 'RF + MIXER + ADC + FPGA + SW' },
  'front-end':      { desc: 'LNA + pre-select filter + (optional) limiter. No mixer, ADC, FPGA.', covers: 'NF, GAIN, LINEARITY, RETURN LOSS' },
  'downconversion': { desc: 'Mixer + LO + IF filter + optional IF amp. No ADC, no FPGA.',         covers: 'PHASE NOISE, IMAGE REJECTION, IF BW' },
  'dsp':            { desc: 'ADC + FPGA/DSP + software. No RF, no mixer.',                        covers: 'SAMPLE RATE, ENOB, FPGA FAMILY' },
};

/* ================================================================
   STAGE 2 — APPLICATIONS — drives arch ranking.
   ================================================================ */
export interface AppDef { id: string; name: string; desc: string; strong_for: string[]; }

export const APPLICATIONS: AppDef[] = [
  { id: 'radar',  name: 'Radar',                 desc: 'Pulsed, coherent, MTI / pulse-compression · X/S/C/Ku-band',   strong_for: ['superhet_double','superhet_single','digital_if','direct_rf_sample','balanced_lna','lna_filter_limiter'] },
  { id: 'ew',     name: 'EW / ESM / ELINT',      desc: 'Threat warning, POI, instantaneous wideband monitoring',       strong_for: ['channelized','digital_if','direct_rf_sample','crystal_video','lna_filter_limiter','multi_band_switched','balanced_lna'] },
  { id: 'sigint', name: 'SIGINT / COMINT',       desc: 'Channelisation, DF, wideband spectral surveillance',           strong_for: ['channelized','digital_if','direct_rf_sample','multi_band_switched','active_antenna'] },
  { id: 'comms',  name: 'Communications',        desc: 'Demod, link-budget-driven — QAM / OFDM / QPSK',                strong_for: ['direct_conversion','low_if','superhet_single','std_lna_filter'] },
  { id: 'satcom', name: 'SATCOM',                desc: 'G/T-driven, tracking receiver, Ku / Ka-band',                  strong_for: ['superhet_double','superhet_single','digital_if','active_antenna','balanced_lna'] },
  { id: 'tnm',    name: 'Test & Measurement',    desc: 'Spectrum analyser, VSA, calibration-grade receiver',           strong_for: ['superhet_double','digital_if','direct_rf_sample','std_lna_filter'] },
  { id: 'instr',  name: 'Lab / Instrumentation', desc: 'Research, prototyping, characterisation',                      strong_for: ['digital_if','direct_rf_sample','std_lna_filter'] },
  { id: 'custom', name: 'Custom / Other',        desc: 'Tell me in free text after the flow.',                         strong_for: [] },
];

/* ================================================================
   STAGE 3 — ARCHITECTURES — scope + app-gated.
   ================================================================ */
export interface ArchDef {
  id: string;
  name: string;
  desc: string;
  scopes: DesignScope[];
  /** Topology family:
   *  - linear / detector  → receiver (baseline wiring)
   *  - tx_linear          → transmitter (linear PA chains — Class A/AB, Doherty, DPD)
   *  - tx_saturated       → transmitter (saturated PAs — Class C/E/F, radar pulse)
   *  - tx_upconversion    → transmitter (IQ mod or mixer-based up-convert front-end) */
  category: 'linear' | 'detector' | 'tx_linear' | 'tx_saturated' | 'tx_upconversion';
  apps_required?: string[];
  /** Which project_type this architecture is offered under. Defaults to
   *  'receiver' for backward compatibility — only the new TX topologies
   *  need to declare themselves. */
  project_type?: 'receiver' | 'transmitter';
}

export const ALL_ARCHITECTURES: ArchDef[] = [
  /* Front-end linear topologies */
  { id: 'std_lna_filter',     name: 'Standard LNA + Pre-select Filter', desc: 'Clean LNA chain with band-pass pre-select. Baseline front-end.',      scopes: ['front-end','full'], category: 'linear' },
  { id: 'balanced_lna',       name: 'Balanced LNA (quad-hybrid)',       desc: 'Two matched LNAs via 90° hybrids — higher IIP3, better input VSWR.',  scopes: ['front-end','full'], category: 'linear' },
  { id: 'lna_filter_limiter', name: 'LNA + Filter + Limiter',           desc: 'Protected front-end — PIN-diode limiter for +40 dBm survivability.',  scopes: ['front-end','full'], category: 'linear' },
  { id: 'active_antenna',     name: 'Active Antenna / Integrated LNA',  desc: 'LNA co-located with antenna feed. Best NF, harder to service.',       scopes: ['front-end','full'], category: 'linear' },
  { id: 'multi_band_switched',name: 'Multi-band Switched Front-End',    desc: 'Band-select switch → per-band LNA+filter. Octave-plus coverage.',     scopes: ['front-end','full'], category: 'linear' },

  /* Downconversion */
  { id: 'superhet_single',    name: 'Single-IF Superheterodyne',              desc: 'One LO + one mixer. Classical comms / radar.',          scopes: ['downconversion','full'], category: 'linear' },
  { id: 'superhet_double',    name: 'Double-IF Superheterodyne',              desc: 'Two LOs — best image rejection + selectivity.',         scopes: ['downconversion','full'], category: 'linear' },
  { id: 'direct_conversion',  name: 'Direct Conversion (Zero-IF / Homodyne)', desc: 'RF → I/Q baseband. No IF. Compact, integrated.',        scopes: ['downconversion','full'], category: 'linear' },
  { id: 'low_if',             name: 'Low-IF Receiver',                        desc: 'IF near DC — avoids DC-offset while staying compact.',  scopes: ['downconversion','full'], category: 'linear' },
  { id: 'image_reject',       name: 'Image-Reject (Hartley / Weaver)',        desc: 'Quadrature mixing cancels image band without filter.',  scopes: ['downconversion','full'], category: 'linear' },

  /* DSP / digital */
  { id: 'direct_rf_sample',   name: 'Direct RF Sampling',          desc: 'RF → ADC directly. No analog mixer. SDR-native.',            scopes: ['dsp','full'], category: 'linear' },
  { id: 'subsampling',        name: 'Subsampling / Undersampling', desc: 'Higher Nyquist zone — needs clean clock + BP filter.',       scopes: ['dsp','full'], category: 'linear' },
  { id: 'digital_if',         name: 'Digital IF / SDR',            desc: 'Analog IF → ADC → FPGA DDC. Most flexible.',                 scopes: ['dsp','full'], category: 'linear' },
  { id: 'channelized',        name: 'Channelised (polyphase FFT)', desc: 'Parallel filter bank — SIGINT / EW simultaneous monitoring.',scopes: ['dsp','full'], category: 'linear' },

  /* Special-purpose detector topologies — gated by application */
  { id: 'crystal_video',      name: 'Crystal Video Detector', desc: 'Schottky-diode power detector. No LO, non-coherent. RWR-class.',        scopes: ['front-end','full'], category: 'detector', apps_required: ['ew','radar'] },
  { id: 'log_video',          name: 'Log-Video Detector',     desc: 'Log-amp detector — wide instantaneous dynamic range, no phase info.',   scopes: ['front-end','full'], category: 'detector', apps_required: ['ew'] },

  { id: 'recommend',          name: 'Not sure — you recommend', desc: 'Architect picks based on your specs + application.', scopes: ['front-end','downconversion','dsp','full'], category: 'linear' },

  /* ============================================================
     Transmitter architectures (project_type="transmitter").
     Split by linearity regime + front-end topology.
     ============================================================ */

  /* Linear TX PA chains */
  { id: 'tx_driver_pa_classab',  name: 'Driver + PA (Class A/AB)',                desc: 'Pre-driver → driver → linear Class-A/AB PA. Baseline comms / SATCOM.',     scopes: ['front-end','full'], category: 'tx_linear',       project_type: 'transmitter' },
  { id: 'tx_doherty',            name: 'Doherty PA',                              desc: 'Main + peaking PA with 90° load-modulation network — high PAE at backoff.', scopes: ['front-end','full'], category: 'tx_linear',       project_type: 'transmitter' },
  { id: 'tx_dpd_linearized',     name: 'DPD-Linearized PA',                       desc: 'Digital predistortion feedback path for EVM / ACLR in 5G NR, wideband LTE.', scopes: ['full'],             category: 'tx_linear',       project_type: 'transmitter' },

  /* Saturated / high-efficiency TX */
  { id: 'tx_class_c_pulsed',     name: 'Class-C / E / F Saturated PA',            desc: 'Non-linear, high-efficiency. Radar pulse, ISM, CW beacons, EW denial.',       scopes: ['front-end','full'], category: 'tx_saturated',    project_type: 'transmitter', apps_required: ['radar','ew','instr','custom'] },
  { id: 'tx_pulse_radar',        name: 'Radar Pulsed PA Chain',                   desc: 'Driver → solid-state PA with gated bias for radar pulse shaping.',             scopes: ['full'],             category: 'tx_saturated',    project_type: 'transmitter', apps_required: ['radar'] },

  /* Upconversion TX front ends */
  { id: 'tx_iq_mod_upconvert',   name: 'IQ-Modulator Upconvert Chain',            desc: 'Baseband I/Q → IQ modulator → driver → PA. Direct-upconvert for comms.',   scopes: ['downconversion','full'], category: 'tx_upconversion', project_type: 'transmitter' },
  { id: 'tx_superhet_upconvert', name: 'Superhet TX (IF → Mixer → PA)',           desc: 'IF source → upconverter mixer → IF/RF filter → driver → PA. Classical SATCOM TX.', scopes: ['downconversion','full'], category: 'tx_upconversion', project_type: 'transmitter' },
  { id: 'tx_direct_dac',         name: 'Direct-DAC Synthesis → PA',               desc: 'RF DAC emits the signal directly, feeding driver → PA. Minimal analog.',     scopes: ['dsp','full'],       category: 'tx_upconversion', project_type: 'transmitter' },

  { id: 'tx_recommend',          name: 'Not sure — you recommend',                desc: 'Architect picks the TX topology from your specs + application.',               scopes: ['front-end','downconversion','dsp','full'], category: 'tx_linear', project_type: 'transmitter' },
];

/* ================================================================
   STAGE 4 — TIER-1 SPECS — scope-filtered, with q_override + advanced flag.
   ================================================================ */
export interface SpecDef {
  id: string;
  q: string;
  q_override?: Partial<Record<DesignScope, string>>;
  drives?: string;
  chips: string[];
  scopes: DesignScope[];
  advanced?: boolean;
}

export const ALL_SPECS: SpecDef[] = [
  { id: 'freq_range',     q: 'Frequency range / band of operation?',            drives: 'LNA + filter topology',                   chips: ['< 2 GHz','2-6 GHz','6-18 GHz','18-40 GHz','Other'],           scopes: ['full','front-end','downconversion'] },
  { id: 'ibw',            q: 'Instantaneous bandwidth (IBW)?',                   drives: 'Filter + IF + ADC planning',               chips: ['< 10 MHz','10-100 MHz','100-500 MHz','500 MHz - 1 GHz','> 1 GHz','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'noise_figure',   q: 'Target system noise figure (dB)?',                 drives: 'LNA + cascade sensitivity',                chips: ['< 2 dB','2-4 dB','4-6 dB','6-10 dB','Other'],                 scopes: ['full','front-end','downconversion'] },
  { id: 'gain',           q: 'Total system gain (dB)?',                          q_override: { 'front-end': 'LNA chain gain (dB)?', 'downconversion': 'RF + IF gain (dB)?' }, drives: 'Cascade gain plan', chips: ['< 20 dB','20-40 dB','40-60 dB','> 60 dB','Auto','Other'], scopes: ['full','front-end','downconversion'] },
  { id: 'selectivity',    q: 'Selectivity / adjacent-channel rejection (dBc)?',  drives: 'IF filter + image-reject topology',        chips: ['40 dBc','60 dBc','80 dBc','> 100 dBc','Other'],               scopes: ['full','downconversion'] },
  { id: 'sfdr',           q: 'SFDR (two-tone, IIP3-driven) in dB?',              drives: 'IIP3 + ADC SFDR',                          chips: ['60 dB','70 dB','80 dB','> 90 dB','Other'],                    scopes: ['full','downconversion','dsp'] },
  { id: 'iip3',           q: 'IIP3 / linearity (dBm)?',                           drives: 'Active-device linearity',                  chips: ['0 dBm','+10 dBm','+20 dBm','+30 dBm','Other'],                scopes: ['full','front-end','downconversion'] },
  { id: 'p1db',           q: 'Output P1dB (dBm)?',                                drives: 'PA / driver-amp backoff',                  chips: ['0 dBm','+10 dBm','+20 dBm','+30 dBm','Other'],                scopes: ['full','downconversion'] },
  { id: 'max_input',      q: 'Max safe input / survivability (dBm)?',             drives: 'Limiter / protection',                     chips: ['+10 dBm','+20 dBm','+30 dBm','+40 dBm','+50 dBm','Other'],    scopes: ['full','front-end'] },
  { id: 'return_loss',    q: 'Input return loss / VSWR?',                         drives: 'Match networks',                           chips: ['-10 dB (2:1)','-14 dB (1.5:1)','-20 dB (1.2:1)','Other'],     scopes: ['full','front-end'] },
  { id: 'power_budget',   q: 'Total power consumption budget (W)?',               drives: 'Regulator + DC-DC topology',               chips: ['< 5 W','5-15 W','15-30 W','> 30 W','Auto','Other'],           scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'supply_voltage', q: 'Primary supply voltage rail?',                      drives: 'Regulator + active-device',                chips: ['+5 V','+12 V','+15 V','+28 V','Multi-rail','Auto','Other'],   scopes: ['full','front-end','downconversion','dsp'] },
  /* Environmental (Tier-1) */
  { id: 'temp_class',     q: 'Operating temperature class?',                      drives: 'Component grade + thermals',               chips: ['Commercial 0 to 70 °C','Industrial -40 to 85 °C','Military -55 to 125 °C','Space / rad-hard','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'vibration',      q: 'Vibration / shock environment?',                    drives: 'Enclosure + connector',                    chips: ['Benign (lab)','MIL-STD-810 light','MIL-STD-810 heavy','Airborne','Naval','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'ip_rating',      q: 'Ingress protection?',                               drives: 'Seal + housing',                           chips: ['IP20 (lab)','IP54 (outdoor)','IP67 (rugged)','IP68','N/A'],   scopes: ['full','front-end','downconversion','dsp'] },
  /* Advanced — hidden behind MDS-lock toggle */
  { id: 'mds_lock',       q: 'Locked MDS / sensitivity (dBm)?',                   drives: 'Constraint that overrides derived value',  chips: ['-90 dBm','-100 dBm','-110 dBm','-120 dBm','-130 dBm','Other'], scopes: ['full','front-end','downconversion'], advanced: true },
];

/* ================================================================
   TRANSMITTER TIER-1 SPECS — shown instead of ALL_SPECS when the
   project was created with project_type='transmitter'. RF-performance
   questions here are all TX-flavoured (Pout / PAE / ACPR / OIP3
   instead of NF / MDS / SFDR).
   ================================================================ */
export const TX_SPECS: SpecDef[] = [
  /* Frequency + bandwidth (shared vocab with RX) */
  { id: 'freq_range',     q: 'Target operating frequency / band?',                 drives: 'PA device technology + match network',     chips: ['< 1 GHz (HF/VHF/UHF)','1-3 GHz (L/S)','3-6 GHz (C)','6-18 GHz (X/Ku)','> 18 GHz (K/Ka/mmWave)','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'ibw',            q: 'Instantaneous (modulation) bandwidth?',              drives: 'Driver + PA BW + matching BW',             chips: ['< 1 MHz','1-20 MHz','20-100 MHz','100-500 MHz','> 500 MHz','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  /* Output power + linearity (TX-specific) */
  { id: 'pout_dbm',       q: 'Target saturated output power Pout_sat (dBm)?',      drives: 'PA device selection + combining',          chips: ['+20 dBm (100 mW)','+30 dBm (1 W)','+37 dBm (5 W)','+40 dBm (10 W)','+47 dBm (50 W)','+50 dBm (100 W)','Other'], scopes: ['full','front-end'] },
  { id: 'p1db_output',    q: 'Target output P1dB (dBm)?',                          drives: 'Backoff from saturation / linearity margin', chips: ['+10 dBm','+20 dBm','+30 dBm','+37 dBm','+40 dBm','Other'],   scopes: ['full','front-end'] },
  { id: 'oip3_dbm',       q: 'Target output IP3 (OIP3, dBm)?',                     drives: 'Driver + PA linearity spec',               chips: ['+30 dBm','+40 dBm','+45 dBm','+50 dBm','Other'],              scopes: ['full','front-end'] },
  { id: 'modulation_tx',  q: 'Modulation / waveform?',                             drives: 'PA class + backoff, DPD requirement',      chips: ['CW','Pulsed','QPSK/OQPSK','16-QAM','64-QAM','256-QAM','OFDM','FMCW','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  /* Spectral purity / compliance */
  { id: 'harmonic_rej',   q: 'Harmonic rejection (dBc at 2f0 / 3f0)?',             drives: 'Post-PA harmonic filter order',            chips: ['-30 dBc','-40 dBc','-50 dBc','-60 dBc','MIL-STD spec','FCC Part 15/97','Other'], scopes: ['full','front-end'] },
  { id: 'aclr_dbc',       q: 'ACPR / ACLR (adjacent-channel, dBc)?',               drives: 'Backoff + DPD linearization need',         chips: ['-30 dBc','-40 dBc','-45 dBc (5G)','-50 dBc (LTE)','-60 dBc','N/A CW','Other'], scopes: ['full','front-end'] },
  { id: 'spur_mask',      q: 'Spurious emission mask?',                            drives: 'Filter topology + shielding',              chips: ['MIL-STD-461','FCC Part 15 Class A','FCC Part 15 Class B','ETSI EN 300','None','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  /* Efficiency + thermal */
  { id: 'pae_pct',        q: 'Power-added efficiency (PAE) target?',               drives: 'PA class selection (AB / Doherty / C/E/F)', chips: ['> 20 % (linear AB)','> 35 % (Doherty)','> 50 % (saturated)','> 65 % (Class E/F)','Other'], scopes: ['full','front-end'] },
  { id: 'supply_voltage', q: 'PA drain supply rail?',                              drives: 'GaN/LDMOS/GaAs selection + DC-DC',         chips: ['+5 V (GaAs)','+12 V','+28 V (GaN)','+48 V (LDMOS)','Multi-rail','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'power_budget',   q: 'Total DC power budget (W)?',                         drives: 'Heatsink / thermal envelope',              chips: ['< 10 W','10-50 W','50-200 W','> 200 W','Auto','Other'],       scopes: ['full','front-end','downconversion','dsp'] },
  /* Duty cycle (pulsed TX) */
  { id: 'duty_cycle',     q: 'Duty cycle (pulsed TX)?',                            drives: 'Gate modulation + thermal average',        chips: ['CW (100%)','> 50%','10-50%','1-10%','< 1% (radar)','Other'], scopes: ['full','front-end'] },
  /* Output protection */
  { id: 'vswr_survival',  q: 'VSWR survivability?',                                drives: 'Circulator / isolator requirement',        chips: ['2:1 (matched)','3:1','5:1','∞:1 (open/short)','Other'],       scopes: ['full','front-end'] },
  /* Environmental — shared with RX */
  { id: 'temp_class',     q: 'Operating temperature class?',                       drives: 'Component grade + thermals',               chips: ['Commercial 0 to 70 °C','Industrial -40 to 85 °C','Military -55 to 125 °C','Space / rad-hard','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'vibration',      q: 'Vibration / shock environment?',                     drives: 'Enclosure + connector',                    chips: ['Benign (lab)','MIL-STD-810 light','MIL-STD-810 heavy','Airborne','Naval','Other'], scopes: ['full','front-end','downconversion','dsp'] },
  { id: 'ip_rating',      q: 'Ingress protection?',                                drives: 'Seal + housing',                           chips: ['IP20 (lab)','IP54 (outdoor)','IP67 (rugged)','IP68','N/A'],   scopes: ['full','front-end','downconversion','dsp'] },
];

/* ================================================================
   WIZARD STATE TYPE — used by helpers + show_if predicates.
   ================================================================ */
export interface WizardState {
  projectType: string | null;
  scope: DesignScope | null;
  application: string | null;
  architecture: string | null;
  specs: Record<string, string>;
  details: Record<string, string>;
  appAnswers: Record<string, string>;
  mdsLockEnabled: boolean;
}

export const emptyWizardState = (): WizardState => ({
  projectType: 'receiver',
  scope: null,
  application: null,
  architecture: null,
  specs: {},
  details: {},
  appAnswers: {},
  mdsLockEnabled: false,
});

/* ================================================================
   STAGE 5 — DEEP DIVES (scope × arch × application).
   Each question has optional show_if(state).
   ================================================================ */
export interface DeepDiveQ {
  id: string;
  q: string;
  chips: string[];
  show_if?: (s: WizardState) => boolean;
}

export interface DeepDiveDef {
  title: string;
  note: string;
  qs: DeepDiveQ[];
}

export const DEEP_DIVES: Record<DesignScope, DeepDiveDef> = {
  'front-end': {
    title: 'RF Front-End deep-dive',
    note: 'Front-end sets the noise floor AND the survivability envelope. Interferer environment usually decides LNA topology + limiter.',
    qs: [
      { id: 'interferer_env', q: 'Strong-interferer / blocker environment?',    chips: ['Low (lab / benign)','Moderate (commercial comms)','High (co-site radar / comms)','Severe (EW / close-in jam)'] },
      { id: 'parent_arch',    q: 'What receiver will your front-end feed?',     chips: ['Superheterodyne','Direct Conversion (homodyne)','Direct RF Sampling / SDR','Digital IF','Unknown — design agnostic'] },
      { id: 'n_channels',     q: 'Number of parallel RF channels?',             chips: ['1','2','4','8','16','Other'] },
      { id: 'antenna_if',     q: 'Antenna interface?',                          chips: ['Single-ended 50Ω','Differential 100Ω','Balun-coupled','Active antenna w/ bias-tee','Other'] },
      { id: 'connector',      q: 'RF connector type?',                          chips: ['SMA','SMP','2.92mm','N-type','K-connector','Other'] },
      { id: 'lna_tech',       q: 'LNA semiconductor technology?',               chips: ['GaAs pHEMT','GaN HEMT','SiGe BiCMOS','CMOS','Auto-pick'] },
      { id: 'filter_tech',    q: 'Pre-select filter technology?',               chips: ['Cavity','SAW','LC discrete','Ceramic','Dielectric resonator','Tunable YIG','Auto'] },
      { id: 'bias_scheme',    q: 'LNA biasing scheme?',                         chips: ['Self-bias','Active bias','Sequenced (neg-then-pos)','Auto'] },
      /* Radar-conditional — TX leakage only when a T/R switch is selected */
      { id: 'tr_switch',      q: 'T/R switching time?',                         chips: ['< 100 ns','< 1 µs','< 10 µs','No T/R switch (separate antennas)'], show_if: s => s.application === 'radar' },
      { id: 'tx_leakage',     q: 'Expected TX leakage at LNA input (dBm)?',    chips: ['< 0 dBm','0 - +10 dBm','+10 - +20 dBm','> +20 dBm'],                show_if: s => s.application === 'radar' && !!s.details?.tr_switch && s.details.tr_switch !== 'No T/R switch (separate antennas)' },
    ],
  },
  'downconversion': {
    title: 'Downconversion / IF-stage deep-dive',
    note: 'These choices determine phase-noise floor, image-rejection ceiling, and tuning agility.',
    qs: [
      { id: 'parent_arch',    q: 'What digitiser / backend will your IF feed?', chips: ['IF-sampling ADC','Zero-IF I/Q ADC pair','External SDR','Analog demod only','Unknown — design agnostic'] },
      { id: 'n_channels',     q: 'Number of simultaneous LO/mixer channels?',   chips: ['1','2','4','8','Other'] },
      { id: 'lo_source',      q: 'LO source / reference?',                      chips: ['TCXO + integer PLL','TCXO + fractional-N PLL','OCXO + PLL','DDS + PLL','External 10 MHz ref','GPS-disciplined','Other'] },
      { id: 'if_freq',        q: 'IF centre frequency?',                        chips: ['70 MHz','140 MHz','500 MHz','1 GHz','Other'], show_if: s => s.architecture !== 'superhet_double' },
      /* Double-IF conditional */
      { id: 'if1_freq',       q: '1st IF centre frequency?',                   chips: ['1 GHz','1.5 GHz','2 GHz','3 GHz','Other'], show_if: s => s.architecture === 'superhet_double' },
      { id: 'if2_freq',       q: '2nd IF centre frequency?',                   chips: ['70 MHz','140 MHz','455 kHz','Other'],      show_if: s => s.architecture === 'superhet_double' },
      /* Image-reject conditional */
      { id: 'ir_topology',    q: 'Image-reject topology?',                      chips: ['Hartley','Weaver','Polyphase filter','Auto'], show_if: s => s.architecture === 'image_reject' },
      /* Zero-IF conditional */
      { id: 'iq_balance',     q: 'Required I/Q balance tolerance?',             chips: ['< 0.1 dB / 0.5°','< 0.5 dB / 2°','< 1 dB / 5°','Auto'], show_if: s => s.architecture === 'direct_conversion' },
      { id: 'baseband_bw',    q: 'Baseband filter bandwidth?',                  chips: ['< 10 MHz','10-100 MHz','> 100 MHz'], show_if: s => s.architecture === 'direct_conversion' || s.architecture === 'low_if' },
      { id: 'if_filter',      q: 'IF filter technology?',                       chips: ['SAW','Crystal','LC discrete','Ceramic','Digital','Auto'] },
      { id: 'phase_noise',    q: 'LO phase noise @ 10 kHz offset (dBc/Hz)?',    chips: ['-90 (TCXO)','-100 (TCXO+PLL)','-110 (low-noise PLL)','-120 (OCXO+PLL)','-130 (high-Q OCXO)','-140 (ruby / premium OCXO)','Auto'] },
      { id: 'tuning_speed',   q: 'Tuning / channel-switch time?',               chips: ['< 1 µs','1-10 µs','10-100 µs','> 100 µs','Other'] },
      { id: 'image_rej',      q: 'Image rejection target (dB)?',                chips: ['30 dB','50 dB','70 dB','> 80 dB','Other'] },
    ],
  },
  'dsp': {
    title: 'Baseband / DSP deep-dive',
    note: 'Clock quality, ENOB, and DSP fabric determine dynamic range and real-time capability. For subsampling, aperture jitter is critical.',
    qs: [
      { id: 'parent_arch',    q: 'Upstream RF block feeding your digitiser?',   chips: ['Superheterodyne (analog IF)','Direct RF (no mixer)','Direct Conversion (I/Q baseband)','Channelised front-end','Unknown — design agnostic'] },
      { id: 'n_channels',     q: 'Number of DDC / channelisation channels?',    chips: ['1','2','4','8','16','32','64','Other'] },
      { id: 'sample_rate',    q: 'ADC sample rate?',                             chips: ['65 Msps','125 Msps','250 Msps','500 Msps','1 Gsps','> 3 Gsps','Other'] },
      { id: 'adc_enob',       q: 'ADC ENOB / resolution?',                       chips: ['10-bit','12-bit','14-bit','16-bit','Other'] },
      { id: 'adc_sfdr',       q: 'ADC SFDR requirement?',                        chips: ['60 dBc','70 dBc','80 dBc','> 90 dBc','Other'] },
      { id: 'clock_jitter',   q: 'Clock aperture jitter budget (fs rms)?',      chips: ['< 50 fs (subsampling-grade)','< 100 fs','< 250 fs','< 500 fs','< 1 ps','Auto'] },
      /* Subsampling-conditional */
      { id: 'nyquist_zone',   q: 'Target Nyquist zone?',                         chips: ['1st (fs/2)','2nd','3rd','4th','Other'], show_if: s => s.architecture === 'subsampling' },
      { id: 'bp_filter_q',    q: 'Band-pass anti-alias filter Q?',               chips: ['Low Q (LC)','Medium (ceramic)','High (SAW)','Cavity'], show_if: s => s.architecture === 'subsampling' },
      { id: 'fpga_family',    q: 'Target FPGA / SoC family?',                    chips: ['Artix-7','Kintex-7','Zynq-7000','Zynq UltraScale+','Versal','Intel Agilex','Other'] },
      { id: 'data_iface',     q: 'Data output interface?',                       chips: ['JESD204B','JESD204C','LVDS','PCIe Gen3','10G Ethernet / VITA49','Other'] },
    ],
  },
  'full': {
    title: 'Full-Receiver deep-dive',
    note: 'End-to-end chain — subset of each block\'s critical params so the BOM is complete.',
    qs: [
      { id: 'interferer_env', q: 'Strong-interferer / blocker environment?',    chips: ['Low','Moderate','High','Severe'] },
      { id: 'n_channels',     q: 'Number of RF channels end-to-end?',           chips: ['1','2','4','8','16','Other'] },
      { id: 'lna_tech',       q: 'LNA technology?',                              chips: ['GaAs pHEMT','GaN HEMT','SiGe','Auto'] },
      { id: 'lo_source',      q: 'LO source?',                                   chips: ['TCXO + PLL','OCXO + PLL','DDS','External ref','Auto'] },
      { id: 'phase_noise',    q: 'LO phase noise @ 10 kHz (dBc/Hz)?',           chips: ['-100','-110','-120','-130','-140','Auto'] },
      { id: 'sample_rate',    q: 'ADC sample rate?',                             chips: ['125 Msps','250 Msps','500 Msps','> 1 Gsps','Auto'] },
      /* Bug A fix — chips normalised to -bit suffix so AUTO_SUGGESTIONS match. */
      { id: 'adc_enob',       q: 'ADC ENOB (bits)?',                             chips: ['12-bit','14-bit','16-bit','Auto'] },
      { id: 'clock_jitter',   q: 'Clock jitter (fs rms)?',                       chips: ['< 100','< 250','< 500','< 1 ps','Auto'] },
      { id: 'fpga_family',    q: 'FPGA / SoC?',                                  chips: ['Zynq UltraScale+','Versal','Kintex-7','Auto'] },
      { id: 'data_iface',     q: 'Data output interface?',                      chips: ['JESD204B/C','LVDS','10GbE / VITA49','Auto'] },
      { id: 'tr_switch',      q: 'T/R switching time?',                         chips: ['< 100 ns','< 1 µs','< 10 µs','N/A (separate antennas)'], show_if: s => s.application === 'radar' },
    ],
  },
};

/* ================================================================
   APPLICATION ADDENDUMS — scope-aware.
   ================================================================ */
export interface AppQDef {
  id: string;
  q: string;
  chips: string[];
  scopes: DesignScope[];
}

export const APP_QUESTIONS: Record<string, { questions: AppQDef[] }> = {
  radar: {
    questions: [
      { id: 'pulse_width', q: 'Pulse width range?',       chips: ['< 100 ns','100 ns - 1 µs','1-10 µs','> 10 µs','CW / LFM','Other'], scopes: ['full','front-end','downconversion','dsp'] },
      { id: 'pri',         q: 'PRI / PRF range?',         chips: ['Fixed','Staggered','Agile / jittered','MTI-compatible'],           scopes: ['full','downconversion','dsp'] },
      { id: 'coherent',    q: 'Coherent processing?',     chips: ['Yes — phase-coherent','No — non-coherent'],                         scopes: ['full','downconversion','dsp'] },
      { id: 'range_res',   q: 'Range resolution target?', chips: ['< 1 m','1-10 m','10-100 m','> 100 m'],                              scopes: ['full','dsp'] },
      /* Front-end-only radar question — phased array / monopulse antenna count.
         Channelised filter bank not applicable (matched filter / pulse compression
         handle spectral work, not a front-end filter bank). */
      { id: 'num_rx_antennas', q: 'Number of receiver antennas (phased array / monopulse)?', chips: ['1','2 (monopulse Δ)','4 (monopulse ΣΔΔ)','8','16','64','128','Other'], scopes: ['front-end'] },
    ],
  },
  ew: {
    questions: [
      { id: 'poi',            q: 'Probability of intercept target?',  chips: ['> 90% @ 100 µs','> 99% @ 1 ms','Auto'],                scopes: ['full','downconversion'] },
      { id: 'df_accuracy',    q: 'Direction-finding accuracy?',       chips: ['< 1° RMS','1-5°','5-15°','N/A — no DF'],               scopes: ['full','downconversion','dsp'] },
      { id: 'simult_signals', q: 'Simultaneous signal handling?',      chips: ['1','2-4','5-16','> 16'],                               scopes: ['full','front-end','downconversion'] },
      { id: 'threat_bands',   q: 'Threat band coverage?',              chips: ['Single band','Multi-band (octave)','Full 0.5-18 GHz','Custom'], scopes: ['full','front-end','downconversion'] },
      /* Front-end-only EW hardware questions — number of RX antennas (DF /
         monopulse / interferometry) and analog channelised filter bank
         (classic RWR / ESM architecture). Gated to scope='front-end' so
         they only appear when the user is designing the LNA/filter chain. */
      { id: 'num_rx_antennas',  q: 'Number of receiver antennas?',                     chips: ['1','2','4','6','8','16','Other'],                 scopes: ['front-end'] },
      { id: 'chan_filter_bank', q: 'Channelised filter bank (number of analog channels)?', chips: ['No — single channel','2','4','8','16','32','64','Other'], scopes: ['front-end'] },
    ],
  },
  sigint: {
    questions: [
      { id: 'chan_bw',    q: 'Per-channel bandwidth?', chips: ['< 1 MHz','1-10 MHz','> 10 MHz'], scopes: ['full','dsp'] },
      { id: 'df',         q: 'DF capability?',         chips: ['Yes','No'],                     scopes: ['full','downconversion','dsp'] },
      { id: 'dwell_time', q: 'Minimum dwell time?',    chips: ['< 1 ms','1-10 ms','> 10 ms'],    scopes: ['full','dsp'] },
      /* Front-end-only SIGINT hardware questions — multi-antenna DF / spatial
         nulling and wideband analog pre-channelisation are core SIGINT patterns. */
      { id: 'num_rx_antennas',  q: 'Number of receiver antennas?',                     chips: ['1','2','4','6','8','16','Other'],                 scopes: ['front-end'] },
      { id: 'chan_filter_bank', q: 'Channelised filter bank (number of analog channels)?', chips: ['No — single channel','2','4','8','16','32','64','Other'], scopes: ['front-end'] },
    ],
  },
  comms: {
    questions: [
      { id: 'modulation',  q: 'Modulation type?',             chips: ['BPSK/QPSK','QAM-16/64/256','OFDM','FM/AM','Custom'], scopes: ['full','dsp'] },
      { id: 'demod',       q: 'Demod location?',              chips: ['Analog','DSP/FPGA','Host CPU'],                      scopes: ['full','dsp'] },
      { id: 'channel_sep', q: 'Adjacent channel separation?', chips: ['< 50 kHz','50-500 kHz','> 500 kHz','Custom'],         scopes: ['full','downconversion'] },
      /* Front-end-only comms question — MIMO / diversity drives antenna count.
         Channelised filter bank not applicable (single-channel front-end is norm). */
      { id: 'num_rx_antennas', q: 'Number of receiver antennas (MIMO / diversity)?', chips: ['1','2','4','8','Other'], scopes: ['front-end'] },
    ],
  },
  satcom: {
    questions: [
      { id: 'gt_target', q: 'G/T target (dB/K)?', chips: ['< 10','10-20','20-30','> 30'],                  scopes: ['full','front-end'] },
      { id: 'tracking',  q: 'Tracking method?',   chips: ['Step-track','Monopulse','Auto-track','None'],  scopes: ['full','front-end'] },
    ],
  },
  tnm:    { questions: [] },
  instr:  { questions: [] },
  custom: { questions: [] },
};

/* ================================================================
   AUTO-SUGGESTIONS — deterministic architect hints keyed by
   question-id → value → advice text.
   ================================================================ */
export const AUTO_SUGGESTIONS: Record<string, Record<string, string>> = {
  interferer_env: {
    'Severe (EW / close-in jam)':    'IIP3 > +20 dBm + PIN-diode limiter strongly recommended. Consider balanced LNA for extra margin.',
    'Severe':                        'IIP3 > +20 dBm + PIN-diode limiter strongly recommended. Consider balanced LNA for extra margin.',
    'High (co-site radar / comms)':  'IIP3 ≥ +15 dBm, limiter optional. Balanced LNA helps input VSWR under co-site conditions.',
    'High':                          'IIP3 ≥ +15 dBm, limiter optional. Balanced LNA helps input VSWR under co-site conditions.',
    'Moderate (commercial comms)':   'IIP3 around +5 to +10 dBm typical. Standard LNA + filter usually sufficient.',
    'Moderate':                      'IIP3 around +5 to +10 dBm typical. Standard LNA + filter usually sufficient.',
    'Low (lab / benign)':            'IIP3 can be relaxed — pick lowest-NF LNA that meets gain target.',
    'Low':                           'IIP3 can be relaxed — pick lowest-NF LNA that meets gain target.',
  },
  noise_figure: {
    '< 2 dB':  'At NF < 2 dB across 6-18 GHz, GaAs pHEMT (~1 dB NF) or GaN HEMT (higher power) are the realistic choices.',
    '2-4 dB':  'Standard GaAs pHEMT or SiGe BiCMOS covers this comfortably.',
  },
  simult_signals: {
    '> 16':  'Plan IIP3 > +20 dBm and consider channelised architecture — single linear path will compress.',
    '5-16':  'IIP3 > +15 dBm recommended; evaluate balanced LNA topology.',
  },
  max_input: {
    '+40 dBm': 'Use PIN-diode limiter ahead of LNA. Recovery time < 100 ns if pulsed environment.',
    '+50 dBm': 'Co-site grade — circulator + limiter combination; consider T/R switch with high isolation.',
  },
  sample_rate: {
    '> 3 Gsps': 'Direct RF sampling territory — clock aperture jitter must be < 100 fs for 60 dB SNR at 6 GHz.',
    '> 1 Gsps': 'Approaching direct-RF — budget < 250 fs aperture jitter to hold > 60 dB SNR at RF > 2 GHz.',
  },
  adc_enob: {
    /* Bug A fix — suggestion keyed on normalised "-bit" chip value now works
     * for both Full-scope and DSP-scope flows. */
    '16-bit': '16-bit ENOB at > 250 Msps drives LVDS → JESD204C. Watch power dissipation.',
  },
  tr_switch: {
    '< 100 ns': 'Fast T/R → solid-state PIN switch. Verify isolation > 60 dB to protect LNA.',
  },
};

/* ================================================================
   CASCADE RULES — deterministic architect sanity checks.
   ================================================================ */
export interface CascadeRule {
  id: string;
  fires: (s: WizardState) => boolean | undefined | string;
  msg: (s: WizardState) => string | null;
  level?: 'warn' | 'ok';
}

export const CASCADE_RULES: CascadeRule[] = [
  {
    id: 'friis_cascade',
    fires: s => !!s.specs.noise_figure,
    msg: s => {
      const nf = s.specs.noise_figure;
      if (nf === '< 2 dB') return `Target system NF < 2 dB → LNA must have NF ≤ 1 dB with gain ≥ 15 dB so Friis makes following stages negligible.`;
      if (nf === '2-4 dB') return `Target NF ${nf} → LNA NF ≤ 2 dB with gain ≥ 12 dB keeps system NF within budget.`;
      return null;
    },
  },
  {
    id: 'gain_stability',
    fires: s => s.specs.gain === '> 60 dB',
    msg: () => `Gain > 60 dB: stability risk from supply/ground/EM coupling. Mitigations → separate shielded cavities per stage, isolated + decoupled supply rails (LC/ferrite), buffer amp between major blocks, reversed input/output orientation. AGC does NOT prevent oscillation — it only manages dynamic range.`,
    level: 'warn',
  },
  {
    /* Bug D fix — superhet-double uses if1_freq (the first IF is the image
     * donor; 2nd IF is already filtered). Single-IF uses if_freq. */
    id: 'freq_plan_image',
    fires: s => (s.architecture === 'superhet_single' || s.architecture === 'superhet_double')
      && !!s.specs.freq_range
      && !!(s.details.if_freq || s.details.if1_freq),
    msg: s => {
      const ifVal = s.details.if1_freq || s.details.if_freq;
      const lbl   = s.architecture === 'superhet_double' ? '1st IF' : 'IF';
      return `Frequency-plan check: with RF ${s.specs.freq_range} and ${lbl} ${ifVal}, image falls at RF ± 2·IF. Verify your pre-select filter attenuates this by ≥ selectivity target.`;
    },
  },
  {
    id: 'subsampling_filter',
    fires: s => s.architecture === 'subsampling',
    msg: () => `Subsampling requires a band-pass anti-alias filter centred on the target Nyquist zone — not a low-pass. Stopband attenuation ≥ desired SFDR. Aperture jitter σ_j × 2π × f_RF sets the ultimate SNR floor.`,
    level: 'warn',
  },
  {
    id: 'direct_rf_clock',
    fires: s => s.architecture === 'direct_rf_sample' && !!s.details.adc_enob,
    msg: s => `Direct RF sampling at ${s.details.adc_enob}: clock aperture jitter < 100 fs RMS needed to preserve SNR above 60 dB at RF > 3 GHz.`,
  },
  {
    id: 'zero_if_offset',
    fires: s => s.architecture === 'direct_conversion',
    msg: () => `Zero-IF watch-list: DC-offset correction loop, I/Q balance < 0.5 dB / 2°, flicker noise corner below IBW lower edge. Baseband HPF eats DC-adjacent signal content.`,
  },
  {
    /* Bug B fix — Hz-normalised comparison so 1 Gsps and 1 Msps aren't
     * conflated by parseFloat. */
    id: 'bw_vs_adc',
    fires: s => !!s.details.sample_rate && !!s.specs.ibw,
    msg: s => {
      const srHzMap: Record<string, number> = {
        '65 Msps': 65e6, '125 Msps': 125e6, '250 Msps': 250e6, '500 Msps': 500e6,
        '1 Gsps': 1e9,   '> 3 Gsps': 3e9,   '> 1 Gsps': 1.5e9,
      };
      const ibwHzMap: Record<string, number> = {
        '< 10 MHz': 10e6, '10-100 MHz': 100e6, '100-500 MHz': 500e6,
        '500 MHz - 1 GHz': 1e9, '> 1 GHz': 2e9,
      };
      const srHz  = srHzMap[s.details.sample_rate];
      const ibwHz = ibwHzMap[s.specs.ibw];
      if (!srHz || !ibwHz) return null;
      if (srHz < 2.5 * ibwHz) {
        return `IBW ${s.specs.ibw} with ADC ${s.details.sample_rate} → Nyquist aliasing risk. Need ≥ 2.5× the highest in-band tone. Consider direct-RF-sample or channelised.`;
      }
      return null;
    },
    level: 'warn',
  },
  {
    /* Bug C fix — coherency lives in downconversion / DSP layers. Don't
     * false-alarm on front-end architectures. */
    id: 'radar_arch_fit',
    fires: s => (s.scope === 'downconversion' || s.scope === 'full')
      && s.application === 'radar'
      && !!s.architecture
      && !['superhet_double','superhet_single','direct_rf_sample','digital_if'].includes(s.architecture),
    msg: s => `Radar + ${archById(s.architecture!)?.name}: phase-coherent processing may be compromised. Verify MTI / Doppler chain compatibility.`,
    level: 'warn',
  },
  {
    id: 'ew_arch_fit',
    fires: s => s.application === 'ew' && !!s.architecture && ['direct_conversion','low_if'].includes(s.architecture),
    msg: () => `EW + direct-conversion / low-IF: POI and simultaneous-signal handling suffer. Consider channelised or digital-IF.`,
    level: 'warn',
  },
];

/* ================================================================
   HELPERS
   ================================================================ */
export function archById(id: string | null): ArchDef | undefined {
  if (!id) return undefined;
  return ALL_ARCHITECTURES.find(a => a.id === id);
}

export function specLabel(c: SpecDef, scope: DesignScope | null): string {
  if (scope && c.q_override?.[scope]) return c.q_override[scope] as string;
  return c.q;
}

export function filterSpecsByScope(
  scope: DesignScope,
  mdsLockEnabled: boolean,
  projectType: string | null = 'receiver',
): { shown: SpecDef[]; hidden: SpecDef[] } {
  // Switch to the TX spec catalogue when the project is a transmitter.
  // TX_SPECS replaces the NF/MDS/SFDR questions with Pout/PAE/ACPR/OIP3
  // — the receiver-flavoured questions are meaningless for a TX chain.
  const source = projectType === 'transmitter' ? TX_SPECS : ALL_SPECS;
  const shown = source.filter(c => {
    if (!c.scopes.includes(scope)) return false;
    if (c.advanced && !mdsLockEnabled) return false;
    return true;
  });
  const hidden = source.filter(c => {
    if (c.advanced) return false;
    return !c.scopes.includes(scope);
  });
  return { shown, hidden };
}

export function filterArchByScopeAndApp(scope: DesignScope, appId: string): {
  linear: ArchDef[]; detector: ArchDef[]; hidden: ArchDef[]; strong: string[];
} {
  const linear = ALL_ARCHITECTURES.filter(a => a.category === 'linear' && a.scopes.includes(scope));
  const detector = ALL_ARCHITECTURES.filter(a => a.category === 'detector'
    && a.scopes.includes(scope)
    && (!a.apps_required || a.apps_required.includes(appId)));
  const hidden = ALL_ARCHITECTURES.filter(a => !a.scopes.includes(scope));
  const app = APPLICATIONS.find(a => a.id === appId);
  const strong = app ? app.strong_for : [];
  const sortFn = (a: ArchDef, b: ArchDef) => {
    const ak = strong.indexOf(a.id) === -1 ? 99 : strong.indexOf(a.id);
    const bk = strong.indexOf(b.id) === -1 ? 99 : strong.indexOf(b.id);
    return ak - bk;
  };
  return { linear: linear.slice().sort(sortFn), detector: detector.slice().sort(sortFn), hidden, strong };
}

/**
 * Transmitter architecture filter — symmetric to `filterArchByScopeAndApp`
 * but returns the TX-specific topologies. Grouped by linearity regime:
 *   - `linear_pa`    Class-A/AB, Doherty, DPD-linearised
 *   - `saturated_pa` Class-C/E/F, pulsed radar
 *   - `upconvert`    IQ-mod, superhet, direct-DAC front ends
 * Currently unused by the wizard (TX UI is pending) but exported so the
 * future TX wizard can import it without another schema change.
 */
export function filterTxArchByScopeAndApp(scope: DesignScope, appId: string): {
  linear_pa: ArchDef[]; saturated_pa: ArchDef[]; upconvert: ArchDef[]; hidden: ArchDef[]; strong: string[];
} {
  const tx = ALL_ARCHITECTURES.filter(a => a.project_type === 'transmitter');
  const inScope = (a: ArchDef) => a.scopes.includes(scope)
    && (!a.apps_required || a.apps_required.includes(appId));
  const linear_pa    = tx.filter(a => a.category === 'tx_linear'       && inScope(a));
  const saturated_pa = tx.filter(a => a.category === 'tx_saturated'    && inScope(a));
  const upconvert    = tx.filter(a => a.category === 'tx_upconversion' && inScope(a));
  const hidden       = tx.filter(a => !a.scopes.includes(scope));
  const app = APPLICATIONS.find(a => a.id === appId);
  const strong = app ? app.strong_for : [];
  return { linear_pa, saturated_pa, upconvert, hidden, strong };
}

export function resolveDeepDiveQs(state: WizardState): { dive: DeepDiveDef | null; qs: DeepDiveQ[] } {
  if (!state.scope) return { dive: null, qs: [] };
  const dive = DEEP_DIVES[state.scope];
  if (!dive) return { dive: null, qs: [] };
  const qs = dive.qs.filter(q => !q.show_if || q.show_if(state));
  return { dive, qs };
}

export function resolveAppQs(state: WizardState): AppQDef[] {
  if (!state.application || !state.scope) return [];
  const a = APP_QUESTIONS[state.application];
  if (!a) return [];
  return a.questions.filter(q => q.scopes.includes(state.scope as DesignScope));
}

export interface InlineSuggestion { qid: string; value: string; msg: string; }

export function allInlineSuggestions(state: WizardState): InlineSuggestion[] {
  const out: InlineSuggestion[] = [];
  const scan = (bucket: Record<string, string>, qid: string) => {
    const v = bucket[qid];
    const m = AUTO_SUGGESTIONS[qid]?.[v];
    if (m) out.push({ qid, value: v, msg: m });
  };
  if (!state.scope) return out;
  const { shown } = filterSpecsByScope(state.scope, state.mdsLockEnabled);
  shown.forEach(q => scan(state.specs, q.id));
  const { qs } = resolveDeepDiveQs(state);
  qs.forEach(q => scan(state.details, q.id));
  resolveAppQs(state).forEach(q => scan(state.appAnswers, q.id));
  return out;
}

export function derivedMDS(state: WizardState): string | null {
  const nf = state.specs.noise_figure;
  const ibw = state.specs.ibw;
  if (!nf || !ibw) return null;
  const nfMap: Record<string, number> = { '< 2 dB': 1.5, '2-4 dB': 3, '4-6 dB': 5, '6-10 dB': 8 };
  const bwMap: Record<string, number> = {
    '< 10 MHz': 5e6, '10-100 MHz': 50e6, '100-500 MHz': 300e6,
    '500 MHz - 1 GHz': 750e6, '> 1 GHz': 2e9,
  };
  const nfDb = nfMap[nf]; const bwHz = bwMap[ibw];
  if (nfDb === undefined || bwHz === undefined) return null;
  const mds = -174 + 10 * Math.log10(bwHz) + nfDb;
  return mds.toFixed(1);
}

export function firedCascadeMessages(state: WizardState): { msg: string; level: 'warn' | 'ok' }[] {
  return CASCADE_RULES
    .filter(r => r.fires(state))
    .map(r => ({ msg: r.msg(state), level: (r.level || 'ok') as 'warn' | 'ok' }))
    .filter(x => x.msg !== null) as { msg: string; level: 'warn' | 'ok' }[];
}

export function archRationale(archId: string, appId: string): string {
  const map: Record<string, Record<string, string>> = {
    std_lna_filter: {
      comms:   'clean LNA + pre-select is the integration-friendly baseline for SoC comms',
      tnm:     'simplest topology — easiest to calibrate and characterise',
      default: 'baseline front-end block — minimum component count',
    },
    balanced_lna: {
      radar:   'high IIP3 keeps the front-end linear under strong in-band clutter returns',
      ew:      'better input VSWR and linearity survive co-site jam environments',
      satcom:  'low input return loss matters for the antenna match budget',
      default: 'higher linearity and return loss than a single-ended LNA',
    },
    lna_filter_limiter: {
      ew:      'high-power survivability is non-negotiable in close-in jam environments',
      radar:   'PIN-diode limiter protects the LNA from T/R leakage during transmit',
      default: 'protected front-end for high-power environments',
    },
    active_antenna: {
      sigint:  'LNA at the antenna eliminates cable loss — crucial when NF budget is tight',
      satcom:  'co-locating the LNA with the feed maximises G/T',
      default: 'noise-floor-critical applications where cable loss matters',
    },
    multi_band_switched: {
      ew:      'one front-end that covers octaves — essential for wideband threat scanning',
      sigint:  'single-box solution across HF to microwave surveillance bands',
      default: 'wide frequency coverage without compromising any single band',
    },
    crystal_video: {
      ew:      'simple, latency-free pulse detector — RWR-class applications',
      default: 'detector-only — no LO, no coherent processing',
    },
    log_video: {
      ew:      'wide instantaneous DR detection — useful alongside a main coherent chain',
      default: 'log-amp detector — wide DR, no phase information',
    },
    superhet_double: {
      radar:   'two-stage downconversion gives image rejection + selectivity your pulse receiver needs',
      satcom:  'low phase noise + high image rejection — key for Ku/Ka link budgets',
      tnm:     'delivers measurement-grade dynamic range and phase stability',
      default: 'best image rejection + phase noise floor in the list',
    },
    superhet_single: {
      comms:   'classical, well-understood — a good default for narrow-band comms',
      default: 'simple mixer-based downconverter',
    },
    digital_if: {
      radar:   'keeps coherent phase while gaining FPGA-side pulse-compression flexibility',
      ew:      'wideband digitising + DDC lets you re-slice the spectrum in firmware',
      sigint:  'single front-end fans out to many FPGA-defined channels',
      default: 'most flexible RX — any modulation, any channelisation, post-capture',
    },
    channelized: {
      ew:      'parallel filter-bank → POI > 99% across wide IBW simultaneously',
      sigint:  'native simultaneous monitoring across the full captured band',
      default: 'parallel channels — use when you need everything at once',
    },
    direct_rf_sample: {
      radar:   'no LO phase noise, zero image issue — best coherency with clean clock',
      tnm:     'minimum analog path → minimum calibration burden',
      default: 'zero mixer, zero image — complexity shifts to the ADC clock tree',
    },
    direct_conversion: {
      comms:   'compact integrated RFIC path — ideal for comms SoCs',
      default: 'compact, single-LO — watch DC offset and I/Q imbalance',
    },
  };
  const key = map[archId];
  if (!key) return 'it matches your scope and application profile';
  return key[appId] || key.default || 'it matches your scope and application profile';
}
