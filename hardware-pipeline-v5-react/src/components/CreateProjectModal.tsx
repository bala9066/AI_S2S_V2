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

/**
 * Infer receiver vs transmitter from the project name so the user doesn't
 * have to pick explicitly. "tx", "transmit", "uplink", "pa", "power amp",
 * "driver" → transmitter; everything else → receiver.
 */
function inferProjectType(name: string): ProjectType {
  const t = name.toLowerCase();
  const txKeywords = ['tx', 'transmit', 'uplink', ' pa ', 'pa chain', 'power amp',
    'driver amp', 'driver amplifier', 'upconvert', 'exciter'];
  if (txKeywords.some(k => t.includes(k))) return 'transmitter';
  return 'receiver';
}

export default function CreateProjectModal({ onConfirm, onCancel }: Props) {
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setLoading(true);
    const dtype = inferDesignType(name);
    const ptype = inferProjectType(name);
    await onConfirm(name.trim(), '', dtype, ptype);
    setLoading(false);
  };

  const inputStyle = {
    width: '100%', background: 'var(--panel2)', border: '1px solid var(--panel3)',
    borderRadius: 5, padding: '10px 13px', fontSize: 13,
    color: 'var(--text)', fontFamily: "'DM Mono', monospace",
    transition: 'border-color 0.2s', outline: 'none', boxSizing: 'border-box' as const,
  };

  const labelStyle = {
    fontSize: 10, color: 'var(--text3)', letterSpacing: '0.12em', marginBottom: 6, display: 'block',
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,11,20,0.88)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999,
    }}>
      <div style={{
        background: 'var(--panel)', border: '1px solid var(--panel2)',
        borderRadius: 10, padding: 30, width: 460,
        boxShadow: '0 24px 60px rgba(0,0,0,0.7)',
      }}>
        <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 17, fontWeight: 800, marginBottom: 6, color: 'var(--text)' }}>
          New Project
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 22 }}>
          Give your project a name — describe your requirements in the chat
        </div>

        {/* Project name */}
        <div style={{ marginBottom: 26 }}>
          <label style={labelStyle}>PROJECT NAME <span style={{ color: 'var(--teal)' }}>*</span></label>
          <input
            style={inputStyle}
            placeholder="e.g. 6-18 GHz wideband receiver, 2.4 GHz 10 W PA chain…"
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) handleSubmit(); }}
            autoFocus
          />
          <div style={{ fontSize: 10, color: 'var(--text4)', marginTop: 6, fontFamily: "'DM Mono', monospace" }}>
            Receiver / transmitter and RF vs digital are inferred from the name
          </div>
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
