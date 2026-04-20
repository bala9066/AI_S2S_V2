import type { PhaseMeta, PhaseStatusValue } from '../types';

interface Props {
  phase: PhaseMeta;
  status: PhaseStatusValue;
}

interface MetricCardProps {
  label: string;
  value: string;
  icon: string;
  color: string;
  accent: string;
  description: string;
}

function MetricCard({ label, value, icon, color, accent, description }: MetricCardProps) {
  return (
    <div style={{
      background: 'var(--panel)', border: `1px solid ${color}22`,
      borderRadius: 10, padding: '18px 20px', flex: 1, minWidth: 160,
      display: 'flex', flexDirection: 'column', gap: 8,
      position: 'relative', overflow: 'hidden',
    }}>
      {/* Decorative glow */}
      <div style={{
        position: 'absolute', top: -20, right: -20,
        width: 80, height: 80, borderRadius: '50%',
        background: `radial-gradient(circle, ${color}18, transparent 70%)`,
        pointerEvents: 'none',
      }} />

      {/* Icon + label */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          width: 28, height: 28, borderRadius: 6,
          background: `${color}14`, border: `1px solid ${color}28`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 14, flexShrink: 0,
        }}>{icon}</span>
        <span style={{
          fontSize: 10, color: 'var(--text4)',
          fontFamily: "'DM Mono', monospace", letterSpacing: '0.1em',
          fontWeight: 500,
        }}>{label}</span>
      </div>

      {/* Value */}
      <div style={{
        fontFamily: "'Syne', sans-serif", fontSize: 20, fontWeight: 800,
        color: accent, lineHeight: 1.1,
      }}>
        {value}
      </div>

      {/* Description */}
      <div style={{ fontSize: 11.5, color: 'var(--text4)', lineHeight: 1.5, fontFamily: "'DM Mono', monospace" }}>
        {description}
      </div>
    </div>
  );
}

const METRIC_DESCRIPTIONS: Record<string, { timeSaved: string; errorReduction: string; confidence: string; costImpact: string }> = {
  P1: {
    timeSaved: 'vs. traditional manual requirement gathering sessions',
    errorReduction: 'fewer specification ambiguity errors downstream',
    confidence: 'requirement completeness verified against domain checklist',
    costImpact: 'estimated annual savings from faster spec cycles',
  },
  P2: {
    timeSaved: 'vs. engineer writing full HRS document manually',
    errorReduction: 'fewer omissions vs. manually authored specs',
    confidence: 'IEEE 29148 section coverage verified automatically',
    costImpact: 'estimated annual savings from automated documentation',
  },
  P3: {
    timeSaved: 'vs. manual multi-standard compliance review',
    errorReduction: 'fewer compliance issues reaching prototype stage',
    confidence: 'rules engine coverage across RoHS, REACH, EMC, safety',
    costImpact: 'estimated annual savings from early compliance detection',
  },
  P4: {
    timeSaved: 'eliminates entire post-layout netlist rework cycles',
    errorReduction: 'fewer PCB spins due to logical connectivity errors',
    confidence: 'electrical rules check coverage across all signal nets',
    costImpact: 'estimated annual savings from reduced prototype respins',
  },
  P6: {
    timeSaved: 'vs. manual FPGA interface definition and spec writing',
    errorReduction: 'fewer glue logic specification errors into RTL',
    confidence: 'interface coverage vs. netlist boundary signals',
    costImpact: 'estimated annual savings from automated GLR generation',
  },
  P8a: {
    timeSaved: 'vs. engineer writing full SRS document manually',
    errorReduction: 'fewer omissions vs. manually authored software specs',
    confidence: 'IEEE 830 traceability matrix coverage',
    costImpact: 'estimated annual savings from automated SRS generation',
  },
  P8b: {
    timeSaved: 'vs. manual software design documentation',
    errorReduction: 'fewer design inconsistencies vs. manual SDD',
    confidence: 'module interface coverage verified against SRS',
    costImpact: 'estimated annual savings from automated SDD generation',
  },
  P8c: {
    timeSaved: 'vs. manual MISRA-C and static analysis review',
    errorReduction: 'code quality issues caught before code review',
    confidence: 'MISRA-C rule coverage across firmware files',
    costImpact: 'estimated annual savings from automated code review',
  },
};

const DEFAULT_DESC = {
  timeSaved: 'compared to traditional manual process',
  errorReduction: 'fewer errors vs. manual execution',
  confidence: 'automation coverage and quality score',
  costImpact: 'estimated annual engineering time savings',
};

export default function MetricsView({ phase, status }: Props) {
  const color = phase.color;
  const m = phase.metrics;
  const desc = METRIC_DESCRIPTIONS[phase.id] || DEFAULT_DESC;
  const isComplete = status === 'completed';

  return (
    <div style={{ paddingTop: 20 }}>
      {/* Header note */}
      <div style={{
        marginBottom: 18, padding: '10px 16px', borderRadius: 7,
        background: `${color}08`, border: `1px solid ${color}18`,
        fontSize: 12, color: 'var(--text3)', fontFamily: "'DM Mono',monospace",
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span style={{ color, fontSize: 13 }}>◎</span>
        {isComplete
          ? `${phase.code} completed — metrics reflect actual AI-assisted savings`
          : `Projected metrics for ${phase.code} — ${phase.name}`
        }
      </div>

      {/* 2×2 metric grid */}
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 20 }}>
        <MetricCard
          label="TIME SAVED"
          value={m.timeSaved}
          icon="⏱"
          color={color}
          accent={color}
          description={desc.timeSaved}
        />
        <MetricCard
          label="ERROR REDUCTION"
          value={m.errorReduction}
          icon="🎯"
          color="#3b82f6"
          accent="#3b82f6"
          description={desc.errorReduction}
        />
      </div>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <MetricCard
          label="CONFIDENCE"
          value={m.confidence}
          icon="✓"
          color="#8b5cf6"
          accent="#8b5cf6"
          description={desc.confidence}
        />
        <MetricCard
          label="ANNUAL COST IMPACT"
          value={m.costImpact}
          icon="₹"
          color="#f59e0b"
          accent="#f59e0b"
          description={desc.costImpact}
        />
      </div>

      {/* Note card */}
      <div style={{
        marginTop: 20, padding: '12px 16px', borderRadius: 8,
        background: 'var(--panel)', border: '1px solid var(--panel3)',
        fontSize: 11.5, color: 'var(--text4)', lineHeight: 1.65,
        fontFamily: "'DM Mono', monospace",
      }}>
        <span style={{ color: 'var(--text3)', fontWeight: 600 }}>Note: </span>
        Metrics are based on industry benchmarks for hardware design automation in defence and industrial electronics.
        Actual savings vary based on design complexity, team experience, and toolchain maturity.
      </div>
    </div>
  );
}
