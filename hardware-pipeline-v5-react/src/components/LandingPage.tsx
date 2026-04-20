interface Props {
  onCreate: () => void;
  onLoad: () => void;
  theme?: 'dark' | 'light';
  onToggleTheme?: () => void;
}

const PHASES_PREVIEW = [
  { code: 'P1', name: 'Design & Requirements', color: '#00c6a7', auto: true },
  { code: 'P2', name: 'HRS Document', color: '#3b82f6', auto: true },
  { code: 'P3', name: 'Compliance Check', color: '#f59e0b', auto: true },
  { code: 'P4', name: 'Netlist Generation', color: '#8b5cf6', auto: true },
  { code: 'P5', name: 'PCB Layout', color: '#475569', auto: false },
  { code: 'P6', name: 'GLR Specification', color: '#00c6a7', auto: true },
  { code: 'P7', name: 'FPGA Design', color: '#475569', auto: false },
  { code: 'P8', name: 'Software Suite', color: '#3b82f6', auto: true },
];

const STEPS = [
  { num: '01', label: 'Describe your design', detail: 'Chat with AI to capture requirements and generate block diagram', color: '#00c6a7' },
  { num: '02', label: 'Pipeline runs automatically', detail: 'AI agents execute P2→P8 sequentially, generating IEEE-standard documents', color: '#3b82f6' },
  { num: '03', label: 'Download & export', detail: 'HRS, compliance matrix, netlist, SRS, SDD, code review — all ready to use', color: '#8b5cf6' },
];

export default function LandingPage({ onCreate, onLoad, theme = 'dark', onToggleTheme }: Props) {
  return (
    <div style={{
      minHeight: '100vh', background: 'var(--navy)',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      position: 'relative', overflow: 'hidden',
    }}>
      {/* Theme toggle — top-right corner */}
      {onToggleTheme && (
        <button
          onClick={onToggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          style={{
            position: 'absolute', top: 18, right: 20, zIndex: 10,
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '6px 12px', borderRadius: 7,
            background: 'var(--panel2)',
            border: '1px solid var(--border)',
            color: 'var(--text2)',
            cursor: 'pointer', fontSize: 13,
            fontFamily: "'DM Mono', monospace",
            letterSpacing: '0.04em',
            transition: 'all 0.2s',
          }}
          onMouseEnter={e => {
            e.currentTarget.style.background = 'var(--panel3)';
            e.currentTarget.style.color = 'var(--teal)';
            e.currentTarget.style.borderColor = 'var(--teal-border)';
          }}
          onMouseLeave={e => {
            e.currentTarget.style.background = 'var(--panel2)';
            e.currentTarget.style.color = 'var(--text2)';
            e.currentTarget.style.borderColor = 'var(--border)';
          }}
        >
          <span>{theme === 'dark' ? '☀' : '☽'}</span>
          <span style={{ fontSize: 10, fontWeight: 700 }}>{theme === 'dark' ? 'LIGHT' : 'DARK'}</span>
        </button>
      )}

      {/* Grid background */}
      <div style={{
        position: 'absolute', inset: 0,
        backgroundImage: 'linear-gradient(var(--panel) 1px, transparent 1px), linear-gradient(90deg, var(--panel) 1px, transparent 1px)',
        backgroundSize: '52px 52px', opacity: 0.5, pointerEvents: 'none',
      }} />

      {/* Glow orb — breathing animation */}
      <div style={{
        position: 'absolute', top: '40%', left: '50%', transform: 'translate(-50%, -50%)',
        width: 700, height: 700, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(0,198,167,0.12) 0%, transparent 65%)',
        pointerEvents: 'none',
        animation: 'orbPulse 4s ease-in-out infinite',
      }} />
      <style>{`
        @keyframes orbPulse {
          0%, 100% { transform: translate(-50%, -50%) scale(1); opacity: 1; }
          50% { transform: translate(-50%, -50%) scale(1.12); opacity: 0.7; }
        }
      `}</style>

      {/* Main content */}
      <div style={{ position: 'relative', textAlign: 'center', maxWidth: 680, padding: '0 24px', width: '100%' }}>
        {/* Sub-brand */}
        <div style={{ fontSize: 10, color: 'var(--teal)', letterSpacing: '0.18em', marginBottom: 20, fontFamily: "'DM Mono', monospace" }}>
          DATA PATTERNS · CODE KNIGHTS
        </div>

        {/* Hero headline */}
        <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 52, fontWeight: 800, lineHeight: 1.05, marginBottom: 12 }}>
          Hardware{' '}
          <span style={{ color: 'var(--teal)', textShadow: '0 0 40px rgba(0,198,167,0.4)' }}>Pipeline</span>
        </div>

        {/* Tagline */}
        <div style={{ fontSize: 15, color: 'var(--text2)', marginBottom: 6, letterSpacing: '0.02em', lineHeight: 1.5 }}>
          AI-powered end-to-end hardware design automation
        </div>
        <div style={{ fontSize: 11, color: 'var(--text4)', letterSpacing: '0.14em', marginBottom: 40, fontFamily: "'DM Mono', monospace" }}>
          DATA PATTERNS INDIA · GREAT AI HACK-A-THON 2026
        </div>

        {/* Metrics strip */}
        <div style={{ display: 'flex', gap: 0, justifyContent: 'center', marginBottom: 40, flexWrap: 'wrap' }}>
          {[
            { before: '8 weeks', after: '4 min', label: 'spec authoring' },
            { before: 'manual', after: 'automated', label: '8 AI phases' },
            { before: 'siloed', after: 'end-to-end', label: 'requirements → code' },
          ].map((m, i) => (
            <div key={i} style={{
              padding: '10px 22px',
              borderRight: i < 2 ? '1px solid var(--border2)' : 'none',
              textAlign: 'center',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifyContent: 'center', marginBottom: 4 }}>
                <span style={{ fontSize: 13, color: 'var(--text4)', fontFamily: "'DM Mono', monospace", textDecoration: 'line-through' }}>{m.before}</span>
                <span style={{ fontSize: 11, color: 'var(--text4)' }}>→</span>
                <span style={{ fontSize: 14, color: '#00c6a7', fontFamily: "'Syne', sans-serif", fontWeight: 800 }}>{m.after}</span>
              </div>
              <div style={{ fontSize: 10, color: 'var(--text4)', fontFamily: "'DM Mono', monospace", letterSpacing: '0.08em' }}>{m.label}</div>
            </div>
          ))}
        </div>

        {/* CTA buttons */}
        <div style={{ display: 'flex', gap: 14, justifyContent: 'center', marginBottom: 52 }}>
          <button onClick={onCreate} style={{
            background: 'var(--teal)', color: 'var(--navy)',
            border: 'none', borderRadius: 6, padding: '13px 36px',
            fontSize: 14, fontFamily: "'DM Mono', monospace", fontWeight: 700,
            cursor: 'pointer', letterSpacing: '0.06em',
            boxShadow: '0 0 24px rgba(0,198,167,0.35)',
            transition: 'all 0.2s',
          }}
            onMouseEnter={e => { e.currentTarget.style.boxShadow = '0 0 40px rgba(0,198,167,0.55)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
            onMouseLeave={e => { e.currentTarget.style.boxShadow = '0 0 24px rgba(0,198,167,0.35)'; e.currentTarget.style.transform = 'translateY(0)'; }}
          >
            + New Project
          </button>
          <button onClick={onLoad} style={{
            background: 'transparent', color: 'var(--text2)',
            border: '1px solid var(--panel3)', borderRadius: 6, padding: '13px 36px',
            fontSize: 14, fontFamily: "'DM Mono', monospace", fontWeight: 500,
            cursor: 'pointer', letterSpacing: '0.06em',
            transition: 'all 0.2s',
          }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--teal)'; e.currentTarget.style.color = 'var(--teal)'; e.currentTarget.style.transform = 'translateY(-1px)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--panel3)'; e.currentTarget.style.color = 'var(--text2)'; e.currentTarget.style.transform = 'translateY(0)'; }}
          >
            Load Existing
          </button>
        </div>

        {/* Pipeline phases preview */}
        <div style={{ marginBottom: 48 }}>
          <div style={{ fontSize: 10, color: 'var(--text4)', letterSpacing: '0.12em', marginBottom: 16, fontFamily: "'DM Mono', monospace" }}>
            8-PHASE PIPELINE
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
            {PHASES_PREVIEW.map((p, i) => (
              <div key={p.code} style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '5px 12px', borderRadius: 20,
                background: `${p.color}0d`,
                border: `1px solid ${p.color}2a`,
                transition: 'all 0.2s',
              }}>
                <div style={{
                  width: 18, height: 18, borderRadius: '50%', flexShrink: 0,
                  background: `${p.color}22`, border: `1.5px solid ${p.color}66`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 9, fontWeight: 800, color: p.color,
                  fontFamily: "'Syne', sans-serif",
                }}>
                  {i + 1}
                </div>
                <span style={{ fontSize: 11, color: p.auto ? 'var(--text2)' : 'var(--text4)', fontFamily: "'DM Mono', monospace" }}>
                  {p.name}
                </span>
                {p.auto ? (
                  <span style={{ fontSize: 9, color: p.color, letterSpacing: '0.05em' }}>AI</span>
                ) : (
                  <span style={{ fontSize: 9, color: 'var(--text4)', letterSpacing: '0.05em' }}>EDA</span>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* How it works */}
        <div style={{
          display: 'flex', gap: 0, background: 'var(--panel)',
          border: '1px solid var(--panel2)', borderRadius: 10, overflow: 'hidden',
        }}>
          {STEPS.map((step, i) => (
            <div key={i} style={{
              flex: 1, padding: '20px 18px', textAlign: 'left',
              borderRight: i < STEPS.length - 1 ? '1px solid var(--panel2)' : 'none',
              position: 'relative',
            }}>
              <div style={{
                fontSize: 28, fontWeight: 800, fontFamily: "'Syne', sans-serif",
                color: `${step.color}22`, lineHeight: 1, marginBottom: 8,
              }}>
                {step.num}
              </div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', marginBottom: 6, lineHeight: 1.3 }}>
                {step.label}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text3)', lineHeight: 1.55 }}>
                {step.detail}
              </div>
              <div style={{
                position: 'absolute', top: 20, right: 16,
                width: 6, height: 6, borderRadius: '50%', background: step.color,
                boxShadow: `0 0 8px ${step.color}`,
              }} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
