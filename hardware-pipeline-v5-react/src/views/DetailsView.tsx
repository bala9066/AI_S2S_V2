import type { PhaseMeta, PhaseStatusValue } from '../types';

interface Props {
  phase: PhaseMeta;
  status: PhaseStatusValue;
}

function Section({ title, icon, items, color }: {
  title: string; icon: string; items: string[]; color: string;
}) {
  return (
    <div style={{
      background: 'var(--panel)', border: `1px solid ${color}22`,
      borderRadius: 8, overflow: 'hidden', flex: 1, minWidth: 200,
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 16px', background: `${color}0c`,
        borderBottom: `1px solid ${color}20`,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span style={{ fontSize: 14 }}>{icon}</span>
        <span style={{
          fontSize: 11, fontFamily: "'DM Mono', monospace",
          color, letterSpacing: '0.1em', fontWeight: 600,
        }}>{title}</span>
        <span style={{
          marginLeft: 'auto', fontSize: 10, color: `${color}88`,
          fontFamily: "'DM Mono', monospace",
          background: `${color}14`, padding: '1px 7px', borderRadius: 10,
        }}>{items.length}</span>
      </div>
      {/* Items */}
      <div style={{ padding: '10px 14px' }}>
        {items.map((item, i) => (
          <div key={i} style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            padding: '5px 0',
            borderBottom: i < items.length - 1 ? `1px solid ${color}10` : 'none',
          }}>
            <span style={{
              width: 5, height: 5, borderRadius: '50%',
              background: color, flexShrink: 0, marginTop: 6,
              opacity: 0.7,
            }} />
            <span style={{
              fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.55,
              fontFamily: "'DM Mono', monospace",
            }}>{item}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ToolChip({ name, color }: { name: string; color: string }) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '6px 13px', borderRadius: 6,
      background: `${color}0d`, border: `1px solid ${color}25`,
      fontSize: 12, color: 'var(--text2)',
      fontFamily: "'DM Mono', monospace",
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, opacity: 0.8 }} />
      {name}
    </div>
  );
}

export default function DetailsView({ phase, status }: Props) {
  const color = phase.color;
  const isComplete = status === 'completed';
  const isRunning = status === 'in_progress';

  return (
    <div style={{ paddingTop: 20 }}>
      {/* Status banner */}
      {isRunning && (
        <div style={{
          marginBottom: 16, padding: '10px 16px', borderRadius: 7,
          background: `rgba(245,158,11,0.08)`, border: `1px solid rgba(245,158,11,0.25)`,
          display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5, color: '#f59e0b',
          fontFamily: "'DM Mono', monospace",
        }}>
          <div style={{
            width: 8, height: 8, borderRadius: '50%',
            border: '2px solid #f59e0b', borderTopColor: 'transparent',
            animation: 'spin 0.8s linear infinite', flexShrink: 0,
          }} />
          Phase is currently running — check Documents tab for live output
        </div>
      )}
      {isComplete && (
        <div style={{
          marginBottom: 16, padding: '10px 16px', borderRadius: 7,
          background: `${color}08`, border: `1px solid ${color}28`,
          display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5, color,
          fontFamily: "'DM Mono', monospace",
        }}>
          <span>✓</span>
          Phase completed — all outputs available in the Documents tab
        </div>
      )}

      {/* Phase description */}
      <div style={{
        marginBottom: 18, padding: '12px 16px', borderRadius: 8,
        background: 'var(--panel)', border: `1px solid ${color}18`,
      }}>
        <div style={{ fontSize: 11, color, fontFamily: "'DM Mono',monospace", letterSpacing: '0.1em', marginBottom: 6 }}>
          PHASE OVERVIEW
        </div>
        <div style={{ fontSize: 13, color: 'var(--text2)', lineHeight: 1.65 }}>
          {phase.tagline}
          {phase.manual && (
            <span style={{ marginLeft: 8, fontSize: 11, color: '#475569',
              background: 'rgba(71,85,105,0.15)', padding: '2px 8px', borderRadius: 10 }}>
              Manual / External
            </span>
          )}
        </div>
      </div>

      {/* Inputs / Outputs in a row */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 18, flexWrap: 'wrap' }}>
        <Section title="INPUTS" icon="⬇" items={phase.inputs} color={color} />
        <Section title="OUTPUTS" icon="⬆" items={phase.outputs} color={color} />
      </div>

      {/* Tools */}
      <div style={{
        background: 'var(--panel)', border: `1px solid ${color}18`,
        borderRadius: 8, overflow: 'hidden', marginBottom: 18,
      }}>
        <div style={{
          padding: '10px 16px', background: `${color}0c`,
          borderBottom: `1px solid ${color}18`,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ fontSize: 14 }}>⚙</span>
          <span style={{
            fontSize: 11, fontFamily: "'DM Mono',monospace",
            color, letterSpacing: '0.1em', fontWeight: 600,
          }}>TOOLS &amp; SERVICES</span>
        </div>
        <div style={{ padding: '12px 14px', display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {phase.tools.map((t, i) => <ToolChip key={i} name={t} color={color} />)}
        </div>
      </div>

      {/* Sub-steps summary */}
      {phase.subSteps.length > 0 && (
        <div style={{
          background: 'var(--panel)', border: `1px solid ${color}18`,
          borderRadius: 8, overflow: 'hidden',
        }}>
          <div style={{
            padding: '10px 16px', background: `${color}0c`,
            borderBottom: `1px solid ${color}18`,
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ fontSize: 14 }}>▸</span>
            <span style={{
              fontSize: 11, fontFamily: "'DM Mono',monospace",
              color, letterSpacing: '0.1em', fontWeight: 600,
            }}>EXECUTION STEPS</span>
            <span style={{
              marginLeft: 'auto', fontSize: 10, color: `${color}88`,
              fontFamily: "'DM Mono',monospace",
              background: `${color}14`, padding: '1px 7px', borderRadius: 10,
            }}>{phase.subSteps.length} steps</span>
          </div>
          <div style={{ padding: '8px 0' }}>
            {phase.subSteps.map((step, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'flex-start', gap: 12,
                padding: '8px 16px',
                borderBottom: i < phase.subSteps.length - 1 ? `1px solid ${color}0e` : 'none',
              }}>
                {/* Step number */}
                <div style={{
                  width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                  background: `${color}14`, border: `1px solid ${color}33`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 10, color, fontFamily: "'DM Mono',monospace", fontWeight: 700,
                  marginTop: 1,
                }}>
                  {i + 1}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                    <span style={{ fontSize: 12.5, color: 'var(--text)', fontWeight: 500 }}>{step.label}</span>
                    <span style={{
                      fontSize: 10, color: `${color}99`,
                      fontFamily: "'DM Mono',monospace",
                      background: `${color}0d`, padding: '1px 6px', borderRadius: 8,
                    }}>{step.time}</span>
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--text3)', lineHeight: 1.55, fontFamily: "'DM Mono',monospace" }}>
                    {step.detail}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
