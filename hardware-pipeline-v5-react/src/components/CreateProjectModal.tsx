import { useState } from 'react';

interface Props {
  onConfirm: (name: string, description: string, design_type: string) => void;
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

export default function CreateProjectModal({ onConfirm, onCancel }: Props) {
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setLoading(true);
    const dtype = inferDesignType(name);
    await onConfirm(name.trim(), '', dtype);
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
        borderRadius: 10, padding: 30, width: 480,
        boxShadow: '0 24px 60px rgba(0,0,0,0.7)',
      }}>
        <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 17, fontWeight: 800, marginBottom: 6, color: 'var(--text)' }}>
          New Project
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 24 }}>
          Give your project a name — describe your design further in the chat
        </div>

        {/* Project name */}
        <div style={{ marginBottom: 28 }}>
          <label style={labelStyle}>PROJECT NAME <span style={{ color: 'var(--teal)' }}>*</span></label>
          <input
            style={inputStyle}
            placeholder="e.g. 2.4GHz RF Transceiver Board"
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
