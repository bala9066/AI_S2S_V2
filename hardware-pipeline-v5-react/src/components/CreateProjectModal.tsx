import { useState } from 'react';
import type { ProjectType } from '../types';

interface Props {
  onConfirm: (name: string, description: string, design_type: string, project_type: ProjectType) => void;
  onCancel: () => void;
}

/** Infer RF vs Digital from the project name — no need to ask the user */
function inferDesignType(name: string): string {
  const text = name.toLowerCase();
  const rfKeywords = ['rf', 'radio', 'antenna', 'ghz', 'mhz', 'frequency', 'amplifier', 'pa ', 'lna',
    'filter', 'mixer', 'oscillator', 'transmit', 'receiv', 'wireless', 'ism', 'radar', 'microwave'];
  if (rfKeywords.some(k => text.includes(k))) return 'rf';
  return 'digital';
}

interface ProjectTypeOption {
  id: ProjectType;
  label: string;
  desc: string;
  examples: string;
}

const PROJECT_TYPE_OPTIONS: ProjectTypeOption[] = [
  {
    id: 'receiver',
    label: 'Receiver',
    desc: 'Antenna → LNA → (mixer) → ADC. Capture + condition incoming signals.',
    examples: 'X-band radar RX · Ku-band SATCOM downconverter · 2-18 GHz wideband',
  },
  {
    id: 'transmitter',
    label: 'Transmitter',
    desc: 'Signal source → driver → PA → harmonic filter → antenna.',
    examples: 'S-band radar TX · Ku-band uplink · 2.4 GHz ISM PA chain',
  },
];

export default function CreateProjectModal({ onConfirm, onCancel }: Props) {
  const [name, setName] = useState('');
  const [projectType, setProjectType] = useState<ProjectType>('receiver');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setLoading(true);
    const dtype = inferDesignType(name);
    await onConfirm(name.trim(), '', dtype, projectType);
    setLoading(false);
  };

  const inputStyle = {
    width: '100%', background: 'var(--panel2)', border: '1px solid var(--panel3)',
    borderRadius: 5, padding: '10px 13px', fontSize: 13,
    color: 'var(--text)', fontFamily: "'DM Mono', monospace",
    transition: 'border-color 0.2s', outline: 'none', boxSizing: 'border-box',
  } as React.CSSProperties;

  const labelStyle = {
    fontSize: 10, color: 'var(--text3)', letterSpacing: '0.12em', marginBottom: 6, display: 'block',
  } as React.CSSProperties;

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,11,20,0.88)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999,
    }}>
      <div style={{
        background: 'var(--panel)', border: '1px solid var(--panel2)',
        borderRadius: 10, padding: 30, width: 520,
        boxShadow: '0 24px 60px rgba(0,0,0,0.7)',
      }}>
        <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 17, fontWeight: 800, marginBottom: 6, color: 'var(--text)' }}>
          New Project
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 22 }}>
          Give your project a name and choose receiver or transmitter — describe the rest in the chat
        </div>

        {/* Project type — RX vs TX */}
        <div style={{ marginBottom: 22 }}>
          <label style={labelStyle}>PROJECT TYPE <span style={{ color: 'var(--teal)' }}>*</span></label>
          <div style={{ display: 'flex', gap: 10 }}>
            {PROJECT_TYPE_OPTIONS.map(opt => {
              const active = projectType === opt.id;
              const accent = opt.id === 'transmitter' ? '#dc2626' : 'var(--teal)';
              return (
                <button
                  key={opt.id}
                  onClick={() => setProjectType(opt.id)}
                  style={{
                    flex: 1, textAlign: 'left',
                    padding: '12px 14px',
                    borderRadius: 6,
                    cursor: 'pointer',
                    background: active
                      ? (opt.id === 'transmitter' ? 'rgba(220,38,38,0.1)' : 'rgba(0,198,167,0.1)')
                      : 'var(--panel2)',
                    border: active ? `1.5px solid ${accent}` : '1px solid var(--panel3)',
                    color: 'var(--text)',
                    fontFamily: "'DM Mono', monospace",
                    transition: 'all 0.15s',
                  }}
                >
                  <div style={{
                    fontSize: 13, fontWeight: 600,
                    color: active ? accent : 'var(--text)',
                    marginBottom: 4,
                    display: 'flex', alignItems: 'center', gap: 6,
                  }}>
                    <span>{opt.label}</span>
                    {active && (
                      <span style={{ fontSize: 10, opacity: 0.8 }}>✓</span>
                    )}
                  </div>
                  <div style={{ fontSize: 10.5, color: 'var(--text3)', lineHeight: 1.45, marginBottom: 3 }}>
                    {opt.desc}
                  </div>
                  <div style={{ fontSize: 9.5, color: 'var(--text4)', fontStyle: 'italic' }}>
                    {opt.examples}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Project name */}
        <div style={{ marginBottom: 26 }}>
          <label style={labelStyle}>PROJECT NAME <span style={{ color: 'var(--teal)' }}>*</span></label>
          <input
            style={inputStyle}
            placeholder={projectType === 'transmitter'
              ? 'e.g. 2.4 GHz 10 W GaN PA Chain'
              : 'e.g. 2.4GHz RF Transceiver Board'}
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) handleSubmit(); }}
            autoFocus
          />
        </div>

        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={onCancel} style={{
            flex: 1, padding: '10px 0', borderRadius: 5, cursor: 'pointer',
            fontSize: 12, fontFamily: "'DM Mono', monospace",
            background: 'transparent', border: '1px solid var(--panel3)',
            color: 'var(--text3)', transition: 'all 0.15s',
          }}>
            Cancel
          </button>
          <button onClick={handleSubmit} disabled={!name.trim() || loading} style={{
            flex: 2, padding: '10px 0', borderRadius: 5, cursor: name.trim() && !loading ? 'pointer' : 'default',
            fontSize: 12, fontFamily: "'DM Mono', monospace", fontWeight: 500,
            background: name.trim() && !loading ? 'var(--teal)' : 'var(--panel2)',
            border: 'none', color: name.trim() && !loading ? 'var(--navy)' : 'var(--text4)',
            transition: 'all 0.15s',
          }}>
            {loading ? 'Creating...' : 'CREATE & START →'}
          </button>
        </div>
      </div>
    </div>
  );
}
