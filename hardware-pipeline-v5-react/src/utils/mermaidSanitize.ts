/**
 * Canonical Mermaid sanitiser — shared by ChatView and (eventually)
 * DocumentsView. Previously lived as a duplicated local function in each
 * view, which allowed the two copies to drift. Extract + test in one place.
 *
 * Fixes everything LLMs tend to mis-emit:
 *   - %% comments and %%{ init }%% frontmatter
 *   - non-ASCII glyphs (Ohm, °, µ, em-dashes, smart quotes, arrows)
 *   - `graph TD` → `flowchart TD`
 *   - `==>` / `->` / unicode arrows → `-->`
 *   - unclosed `[` brackets from multi-line labels
 *   - bad chars inside node labels (`<>()"'#|@` etc.)
 *   - missing arrows between bare identifiers
 *   - `end` keyword collapsed onto the same line as content
 */

/** Sanitise AI-generated Mermaid code. */
export function sanitizeMermaid(raw: string): string {
  let code = raw.trim().replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  // Strip %%{ init }%% frontmatter and %% comments
  code = code.replace(/^%%\{[\s\S]*?\}%%\s*/m, '');
  code = code.replace(/%%[^\n]*/g, '');
  // Replace non-ASCII symbols that break Mermaid parser
  const uMap: Record<string, string> = {
    '\u03A9': 'Ohm', '\u2126': 'Ohm', '\u00B0': 'deg', '\u00B5': 'u',
    '\u2013': '-', '\u2014': '-', '\u2018': "'", '\u2019': "'",
    '\u201C': '"', '\u201D': '"', '\u2264': '<=', '\u2265': '>=',
    '\u00B1': '+-', '\u2192': '-->', '\u2190': '<--',
  };
  code = code.replace(/[^\x00-\x7F]/g, ch => uMap[ch] || '');
  // Fix double-paren nodes ((label)) → (label)
  code = code.replace(/\(\(([^)]*)\)\)/g, '($1)');
  // Round-bracket nodes with quoted labels containing nested parens break
  // the paren-label sanitiser below — its regex `/\(([^)]*)\)/g` captures up
  // to the *first* `)`, so `S11("VGA (AGC)…")` is parsed as a node whose
  // label ends at `(AGC)`, leaving the rest of the original label as
  // syntactically-garbage tokens on the line and failing the whole diagram.
  // Normalise `ID("…(…)…")` → `ID["…(…)…"]` so the bracket regex (which
  // uses `[^\]]*` and can span any inner `()`) finds the right boundary.
  // The rounded-edge visual is lost for these nodes, but the diagram
  // actually renders — strictly better than dumping the raw source.
  code = code.replace(/([\w-]+)\("([^"]*[()][^"]*)"\)/g, '$1["$2"]');
  // Arrow normalisations
  code = code.replace(/\u2014\u2014>/g, '-->').replace(/\u2014>/g, '-->');
  code = code.replace(/——>/g, '-->').replace(/—>/g, '-->');
  code = code.replace(/==>/g, '-->');
  code = code.replace(/(\w)\s*->\s*(\w)/g, '$1 --> $2');
  // Normalise graph → flowchart
  code = code.replace(/^graph\s+(TD|LR|TB|RL|BT)/im, 'flowchart $1');
  code = code.replace(/^(flowchart)\n(TD|LR|TB|RL|BT)\b/m, '$1 $2');
  // Join lines where [ is opened but not closed (multi-line node labels)
  {
    const joinedLines: string[] = [];
    for (const line of code.split('\n')) {
      if (joinedLines.length > 0) {
        const last = joinedLines[joinedLines.length - 1];
        const opens = (last.match(/\[/g) || []).length;
        const closes = (last.match(/\]/g) || []).length;
        if (opens > closes) {
          joinedLines[joinedLines.length - 1] = last.trimEnd() + ' ' + line.trimStart();
          continue;
        }
      }
      joinedLines.push(line);
    }
    code = joinedLines.join('\n');
    // Auto-close any still-unclosed [ on a single line (LLM forgot closing bracket)
    code = code.split('\n').map(line => {
      const opens = (line.match(/\[/g) || []).length;
      const closes = (line.match(/\]/g) || []).length;
      return opens > closes ? line + ']'.repeat(opens - closes) : line;
    }).join('\n');
  }
  // Ensure known diagram type on line 1
  const first = code.split('\n')[0].trim().toLowerCase();
  const known = ['flowchart', 'sequencediagram', 'classdiagram', 'statediagram',
    'erdiagram', 'gantt', 'pie', 'gitgraph', 'mindmap', 'timeline'];
  if (!known.some(t => first.startsWith(t))) code = 'flowchart TD\n' + code;
  // Literal \n → space; strip all HTML tags; decode HTML entities
  code = code.replace(/\\n/g, ' ');
  code = code.replace(/&lt;/g, '(').replace(/&gt;/g, ')').replace(/&amp;/g, 'and').replace(/&nbsp;/g, ' ');
  code = code.replace(/<[^>]+>/gi, ' ');
  // Ensure `end` (subgraph close) is always on its own line — trailing case
  code = code.split('\n').map(line => {
    if (/\bend\s*$/.test(line) && !/^\s*end\b/.test(line)) {
      const before = line.replace(/\s+end\s*$/, '').trimEnd();
      return (before ? before + '\n' : '') + 'end';
    }
    return line;
  }).join('\n');
  // Ensure `end` is always on its own line — leading case ("end NODE ...")
  code = code.split('\n').map(line => {
    const m = line.match(/^(\s*)end\s+(\S.*)$/);
    if (m) return `${m[1]}end\n${m[1]}${m[2]}`;
    return line;
  }).join('\n');
  // Fix "NODE |label|" (no following node) → "NODE[label]" — orphan pipe-label
  code = code.split('\n').map(line =>
    line.replace(/(\w)\s+\|([^|]+)\|(?!\s*[\w\[])/g,
      (_m, pre, inner) => `${pre}[${inner.trim()}]`)
  ).join('\n');
  // Fix "NODEA |label| NODEB" (pipe label but NO arrow) → "NODEA -->|label| NODEB"
  code = code.split('\n').map(line => {
    if (/^\s*(subgraph|end|%%)/.test(line)) return line;
    return line.replace(
      /^(\s*)([\w][\w\-]*)\s+(\|[^|]+\|)\s*([\w])/,
      (_m, indent, n1, label, n2start) => `${indent}${n1} -->${label} ${n2start}`
    );
  }).join('\n');
  // Fix two+ word-tokens on same line with NO arrow — handles both "NODEA NODEB[" and "A B C" (3+ bare IDs)
  code = code.split('\n').map(line => {
    const stripped = line.trim();
    if (!stripped || /^\s*(subgraph|end|%%)/.test(line)) return line;
    line = line.replace(
      /^(\s*)([\w][\w\-]*)(\s+)([\w][\w\-]*[\[\(])/,
      (_m, indent, n1, _sp, n2) => `${indent}${n1} --> ${n2}`
    );
    if (/-->|---/.test(line)) return line;
    const indent = line.match(/^(\s*)/)?.[1] || '';
    const tokens = stripped.split(/\s+/);
    const seqKeywords = /^(participant|actor|activate|deactivate|Note|loop|alt|else|opt|par|rect|end|autonumber|title|as)\b/i;
    if (tokens.length >= 3 && tokens.every(t => /^[\w][\w\-]*$/.test(t)) && !seqKeywords.test(stripped)) {
      return indent + tokens.join(' --> ');
    }
    return line;
  }).join('\n');
  // Fix "NODEA[label] NODEB[label]" — bracket-delimited nodes without arrow between.
  code = code.split('\n').map(line => {
    if (/^\s*(subgraph|end|%%)/.test(line)) return line;
    return line.replace(
      /([\]\)\}])(\s+)([\w][\w\-]*)(\s*[\[\(\{])/g,
      (_m, closer, _sp, n2, opener) => `${closer} --> ${n2}${opener}`
    );
  }).join('\n');
  // Fix "NODEA] NODEID |label| NODEB" — pipe-label after a bare identifier that follows a closed node.
  code = code.split('\n').map(line => {
    if (/^\s*(subgraph|end|%%)/.test(line)) return line;
    return line.replace(
      /([\]\)\}])\s+([\w][\w\-]*)\s+(\|[^|]+\|)/g,
      (_m, closer, node, label) => `${closer} --> ${node} -->${label}`
    );
  }).join('\n');
  // Fix "NODE [label]" → "NODE[label]"
  code = code.split('\n').map(line => {
    if (/^\s*subgraph\b/.test(line)) return line.replace(/^(\s*subgraph\s+[\w-]+)\s+\[/, '$1[');
    return line.replace(/(\w)\s+\[/g, '$1[');
  }).join('\n');
  // Sanitize node labels
  const sanitizeLabel = (inner: string) =>
    inner
      .replace(/-->/g, ' ').replace(/->/g, ' ')
      .replace(/</g, ' ').replace(/>/g, ' ')
      .replace(/\(/g, ' ').replace(/\)/g, ' ')
      .replace(/_/g, '-')
      .replace(/&(?!amp;|lt;|gt;|#)/g, 'and')
      .replace(/"/g, ' ').replace(/'/g, ' ')
      .replace(/#/g, ' ')
      .replace(/\|/g, '/')
      .replace(/@/g, ' ')
      .replace(/-{2,}/g, ' ')
      .replace(/^[-—=]+|[—=-]+$/g, ' ')
      .replace(/[\[\]]/g, ' ')
      .replace(/\s{2,}/g, ' ')
      .trim();
  code = code.replace(/\[([^\]]*)\]/g, (_m, inner: string) => `[${sanitizeLabel(inner)}]`);
  code = code.replace(/\(([^)]*)\)/g, (_m, inner: string) => `(${sanitizeLabel(inner)})`);
  code = code.replace(/\{([^}]*)\}/g, (_m, inner: string) => `{${sanitizeLabel(inner)}}`);
  code = code.replace(/"([^"]+)"/g, (_m, inner: string) => `"${sanitizeLabel(inner)}"`);
  // Edge labels: --> |label| node
  code = code.replace(/\|([^|]+)\|/g, (_m, inner: string) => `|${sanitizeLabel(inner)}|`);
  // State diagram colon-label sanitization — use spaces NOT parentheses
  if (first.startsWith('statediagram')) {
    code = code.split('\n').map(line => {
      const m = line.match(/^(\s*.*?-->\s*\S+\s*:)(.*)$/);
      if (m) {
        const label = m[2].replace(/>/g, ' ').replace(/</g, ' ').replace(/:/g, ',');
        return m[1] + label;
      }
      return line;
    }).join('\n');
  }
  return code;
}
