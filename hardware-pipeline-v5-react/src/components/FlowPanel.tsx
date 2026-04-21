import { useState, useEffect, useRef } from 'react';
import type { PhaseMeta, PhaseStatusValue, DesignScope } from '../types';
import { isPhaseApplicable } from '../data/phases';

// Parse "12s" → 12, "30 min" → 1800, "2 hrs" → 7200, "1-2 days" → 108000
function parseStepSeconds(time: string): number {
  const s = time.match(/^(\d+)s$/i);
  if (s) return parseInt(s[1]);
  const min = time.match(/(\d+)(?:-\d+)?\s*min/i);
  if (min) return parseInt(min[1]) * 60;
  const hr = time.match(/(\d+(?:\.\d+)?)(?:-\d+)?\s*hr/i);
  if (hr) return parseFloat(hr[1]) * 3600;
  const day = time.match(/(\d+)(?:-\d+)?\s*day/i);
  if (day) return parseInt(day[1]) * 86400;
  return 30;
}

interface Props {
  phase: PhaseMeta;
  status: PhaseStatusValue;
  onExecute?: () => void;
  pipelineRunning?: boolean;
  /** Project's wizard-selected scope — disables Run when phase is not applicable. */
  scope?: DesignScope | null;
  onNotApplicable?: () => void;
}

export default function FlowPanel({ phase, status, onExecute, pipelineRunning, scope, onNotApplicable }: Props) {
  const color = phase.color;
  const isRunning = status === 'in_progress';
  const isComplete = status === 'completed';
  const isFailed = status === 'failed';

  const [activeStep, setActiveStep] = useState(-1);
  const [stepProgress, setStepProgress] = useState(0);
  const [doneSteps, setDoneSteps] = useState<Set<number>>(new Set());
  const startRef = useRef<number | null>(null);
  const prevStatusRef = useRef<PhaseStatusValue | null>(null);

  // Reset / sync animation when status changes
  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;

    if (status === 'completed') {
      setActiveStep(-1);
      setStepProgress(100);
      setDoneSteps(new Set(phase.subSteps.map((_, i) => i)));
      startRef.current = null;
      return;
    }
    if (status === 'pending' || status === 'failed') {
      setActiveStep(-1);
      setStepProgress(0);
      setDoneSteps(new Set());
      startRef.current = null;
      return;
    }
    if (status === 'in_progress' && prev !== 'in_progress') {
      // Fresh start
      startRef.current = Date.now();
      setActiveStep(0);
      setStepProgress(0);
      setDoneSteps(new Set());
    }
  }, [status, phase.subSteps]);

  // Tick animation while running — updates at 4fps (250ms) for smooth bars
  useEffect(() => {
    if (!isRunning) return;
    if (!startRef.current) startRef.current = Date.now();

    const stepTimes = phase.subSteps.map(s => parseStepSeconds(s.time));
    const totalTime = stepTimes.reduce((a, b) => a + b, 0);

    const tick = () => {
      const elapsed = (Date.now() - startRef.current!) / 1000;
      if (elapsed >= totalTime) {
        // Animation exhausted — hold last step at 99% until status flips to 'completed'
        setActiveStep(stepTimes.length - 1);
        setStepProgress(99);
        setDoneSteps(new Set(Array.from({ length: stepTimes.length - 1 }, (_, i) => i)));
        return;
      }
      let cum = 0;
      for (let i = 0; i < stepTimes.length; i++) {
        if (elapsed < cum + stepTimes[i]) {
          const prog = Math.min(((elapsed - cum) / stepTimes[i]) * 100, 99);
          setActiveStep(i);
          setStepProgress(prog);
          setDoneSteps(new Set(Array.from({ length: i }, (_, j) => j)));
          return;
        }
        cum += stepTimes[i];
      }
    };

    tick();
    const iv = setInterval(tick, 250);
    return () => clearInterval(iv);
  }, [isRunning, phase.subSteps]);

  const applicable = isPhaseApplicable(phase, scope ?? undefined);
  const canRun = !!onExecute && !phase.manual && phase.id !== 'P1'
    && !isRunning && !(pipelineRunning && !isRunning) && applicable;

  const btnLabel = !applicable
    ? 'NOT APPLICABLE'
    : isRunning
    ? 'Running...'
    : isComplete || isFailed
    ? `\u21BA Re-run ${phase.code}`
    : `\u25B6 Run ${phase.code}`;

  return (
    <div style={{
      width: 300, flexShrink: 0,
      borderLeft: '1px solid var(--border2)',
      background: 'var(--navy)',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Sticky header */}
      <div style={{
        padding: '14px 16px 10px',
        borderBottom: '1px solid var(--border2)',
        background: 'var(--navy)',
        flexShrink: 0,
      }}>
        <div style={{ fontSize: 9, color, fontFamily: "'DM Mono',monospace", letterSpacing: '0.14em', marginBottom: 4 }}>
          EXECUTION FLOW
        </div>
        <div style={{ fontFamily: "'Syne',sans-serif", fontSize: 13, fontWeight: 800, color: 'var(--text)', lineHeight: 1.25, marginBottom: 6 }}>
          {phase.code} — {phase.name}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            fontSize: 9, color: 'var(--text4)',
            background: 'var(--panel2)', border: '1px solid var(--panel3)',
            padding: '1px 7px', borderRadius: 10,
            fontFamily: "'DM Mono',monospace",
          }}>
            {phase.subSteps.length} steps
          </span>
          <span style={{
            fontSize: 9, color: 'var(--text4)',
            background: 'var(--panel2)', border: '1px solid var(--panel3)',
            padding: '1px 7px', borderRadius: 10,
            fontFamily: "'DM Mono',monospace",
          }}>
            {phase.time}
          </span>
          {isRunning && (
            <span style={{
              fontSize: 9, color, background: `${color}12`,
              border: `1px solid ${color}33`, padding: '1px 7px', borderRadius: 10,
              fontFamily: "'DM Mono',monospace", animation: 'pulse 1.5s ease infinite',
            }}>
              LIVE
            </span>
          )}
          {isComplete && (
            <span style={{
              fontSize: 9, color, background: `${color}12`,
              border: `1px solid ${color}33`, padding: '1px 7px', borderRadius: 10,
              fontFamily: "'DM Mono',monospace",
            }}>
              DONE
            </span>
          )}
        </div>
      </div>

      {/* Steps list — scrollable */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px' }}>
        {phase.manual ? (
          /* Manual phase — static list, no animation */
          <div style={{ fontSize: 12, color: 'var(--text4)', fontFamily: "'DM Mono',monospace", lineHeight: 1.65 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, padding: '8px 12px', background: 'rgba(71,85,105,0.1)', border: '1px solid rgba(71,85,105,0.25)', borderRadius: 7 }}>
              <span style={{ fontSize: 16 }}>⚙</span>
              <span style={{ fontSize: 11.5, color: 'var(--text3)' }}>
                This phase is completed in {phase.externalTool || 'an external EDA tool'}.
              </span>
            </div>
            {phase.subSteps.map((step, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
                <div style={{
                  width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                  border: '1px solid var(--border2)', background: 'var(--panel)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 9, color: 'var(--text4)', fontFamily: "'DM Mono',monospace", fontWeight: 700, marginTop: 1,
                }}>{i + 1}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 1 }}>{step.label}</div>
                  <div style={{ fontSize: 10, color: '#475569', fontFamily: "'DM Mono',monospace" }}>{step.time}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          phase.subSteps.map((step, i) => {
            const isDone = doneSteps.has(i) || isComplete;
            const isActive = activeStep === i && isRunning;
            const stepColor = isDone ? color : isActive ? color : 'var(--border2)';

            return (
              <div key={i} style={{ display: 'flex', gap: 10 }}>
                {/* Circle + vertical connector */}
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
                  <div style={{
                    width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                    border: `2px solid ${isDone || isActive ? color : 'var(--border2)'}`,
                    background: isDone ? `${color}18` : isActive ? `${color}0d` : 'var(--panel)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 9, fontFamily: "'DM Mono',monospace", fontWeight: 700,
                    color: isDone || isActive ? color : '#475569',
                    transition: 'all 0.4s',
                    boxShadow: isActive ? `0 0 12px ${color}44` : isDone ? `0 0 6px ${color}22` : 'none',
                  }}>
                    {isDone ? '✓' : i + 1}
                  </div>
                  {i < phase.subSteps.length - 1 && (
                    <div style={{
                      width: 2, flex: 1, minHeight: 14,
                      background: isDone ? `${color}44` : 'var(--border2)',
                      margin: '3px 0',
                      transition: 'background 0.4s',
                    }} />
                  )}
                </div>

                {/* Content */}
                <div style={{ flex: 1, paddingBottom: i < phase.subSteps.length - 1 ? 10 : 0 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginBottom: 3 }}>
                    <span style={{
                      flex: 1, fontSize: 11.5, lineHeight: 1.3,
                      color: isDone ? 'var(--text)' : isActive ? 'var(--text)' : 'var(--text4)',
                      fontWeight: isActive ? 600 : isDone ? 500 : 400,
                      transition: 'color 0.3s',
                    }}>{step.label}</span>
                    <span style={{
                      fontSize: 9, flexShrink: 0,
                      color: isDone || isActive ? color : '#475569',
                      fontFamily: "'DM Mono',monospace",
                      background: isDone || isActive ? `${color}0d` : 'transparent',
                      padding: '1px 5px', borderRadius: 4,
                      transition: 'all 0.3s',
                    }}>{step.time}</span>
                  </div>

                  {/* Detail text — when active or complete */}
                  {(isActive || isDone) && (
                    <div style={{
                      fontSize: 10.5, color: 'var(--text4)', lineHeight: 1.5,
                      fontFamily: "'DM Mono',monospace", marginBottom: isActive ? 6 : 0,
                    }}>
                      {step.detail}
                    </div>
                  )}

                  {/* Animated progress bar for active step */}
                  {isActive && (
                    <div style={{
                      height: 3, background: 'var(--panel2)', borderRadius: 2,
                      overflow: 'hidden', marginTop: 4,
                    }}>
                      <div style={{
                        height: '100%', borderRadius: 2,
                        background: `linear-gradient(90deg, ${color}77, ${color})`,
                        width: `${stepProgress}%`,
                        transition: 'width 0.2s linear',
                        boxShadow: `0 0 8px ${color}66`,
                      }} />
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}

        {/* Completion summary card */}
        {isComplete && (
          <div style={{
            marginTop: 14, padding: '12px 14px',
            background: `${color}0a`, border: `1px solid ${color}28`,
            borderRadius: 8,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 10 }}>
              <span style={{ fontSize: 15, color }}>✓</span>
              <span style={{ fontFamily: "'Syne',sans-serif", fontSize: 12, fontWeight: 800, color }}>Phase Complete</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {[
                { label: 'STEPS', value: String(phase.subSteps.length) },
                { label: 'TIME', value: phase.time },
                { label: 'SAVED', value: phase.metrics.timeSaved.split(' → ')[0] },
                { label: 'CONFIDENCE', value: phase.metrics.confidence },
              ].map(({ label, value }) => (
                <div key={label} style={{ background: `${color}08`, borderRadius: 5, padding: '6px 8px' }}>
                  <div style={{ fontSize: 8, color: 'var(--text4)', fontFamily: "'DM Mono',monospace", letterSpacing: '0.1em', marginBottom: 2 }}>{label}</div>
                  <div style={{ fontSize: 12, color, fontWeight: 700, fontFamily: "'Syne',sans-serif" }}>{value}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Run / Re-run button at bottom */}
      {!phase.manual && phase.id !== 'P1' && onExecute && (
        <div style={{
          padding: '12px 14px',
          borderTop: '1px solid var(--border2)',
          background: 'var(--navy)',
          flexShrink: 0,
        }}>
          <button
            onClick={canRun ? onExecute : (!applicable && onNotApplicable ? onNotApplicable : undefined)}
            disabled={!canRun && applicable}
            title={!applicable ? `Not applicable for design_scope ${scope ?? 'full'}` : undefined}
            style={{
              width: '100%', padding: '9px 12px', borderRadius: 6,
              border: `1px solid ${isRunning ? `${color}33` : canRun ? `${color}66` : `${color}25`}`,
              background: isRunning ? `${color}06` : canRun ? `${color}16` : `${color}06`,
              color: isRunning ? `${color}66` : canRun ? color : `${color}44`,
              fontSize: 11.5, fontFamily: "'DM Mono',monospace", fontWeight: 700,
              cursor: canRun ? 'pointer' : 'not-allowed',
              letterSpacing: '0.07em', transition: 'all 0.2s',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
            }}
            onMouseEnter={e => { if (canRun) { e.currentTarget.style.background = `${color}22`; e.currentTarget.style.boxShadow = `0 0 12px ${color}33`; } }}
            onMouseLeave={e => { if (canRun) { e.currentTarget.style.background = `${color}16`; e.currentTarget.style.boxShadow = 'none'; } }}
          >
            {isRunning ? (
              <>
                <div style={{ width: 9, height: 9, borderRadius: '50%', border: `2px solid ${color}66`, borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
                Running...
              </>
            ) : btnLabel}
          </button>
        </div>
      )}
    </div>
  );
}
