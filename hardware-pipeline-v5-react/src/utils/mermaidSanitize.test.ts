/**
 * sanitizeMermaid — regression tests for the B6-family of Mermaid parse errors.
 *
 * Each test uses a single known-bad input the LLM actually produced in the
 * wild (or a trivially derived variant) and asserts the sanitiser rewrites
 * it into something Mermaid 10 will accept.
 */
import { describe, expect, it } from 'vitest';
import { sanitizeMermaid } from './mermaidSanitize';

const san = sanitizeMermaid;

describe('frontmatter + comments', () => {
  it('strips %%{ init }%% frontmatter', () => {
    const out = san("%%{ init: { theme: 'dark' } }%%\ngraph TD\nA-->B");
    expect(out).not.toContain('%%{');
    expect(out).not.toContain('init');
  });

  it('strips standalone %% comment lines', () => {
    const out = san('flowchart TD\n%% this is a comment\nA-->B');
    expect(out).not.toContain('this is a comment');
    expect(out).toContain('A-->B');
  });
});

describe('arrow normalisation', () => {
  it('converts ==> to -->', () => {
    const out = san('flowchart TD\nA==>B');
    expect(out).toMatch(/A\s*-->\s*B/);
    expect(out).not.toContain('==>');
  });

  it('converts word -> word to -->', () => {
    const out = san('flowchart TD\nA->B');
    expect(out).toMatch(/A\s*-->\s*B/);
  });

  it('converts em-dash arrow (——>) to -->', () => {
    const out = san('flowchart TD\nA——>B');
    expect(out).toMatch(/A\s*-->\s*B/);
  });
});

describe('diagram type normalisation', () => {
  it('rewrites `graph TD` to `flowchart TD`', () => {
    const out = san('graph TD\nA-->B');
    expect(out).toMatch(/^flowchart TD/);
    expect(out).not.toMatch(/^graph TD/);
  });

  it('merges `flowchart\\nTD` into `flowchart TD`', () => {
    const out = san('flowchart\nTD\nA-->B');
    expect(out).toMatch(/^flowchart TD/);
  });

  it('prepends `flowchart TD` when diagram type is missing', () => {
    const out = san('A-->B');
    expect(out).toMatch(/^flowchart TD/);
  });
});

describe('label sanitisation', () => {
  it('strips angle brackets from node labels', () => {
    const out = san('flowchart TD\nA[Signal <200MHz>]');
    expect(out).not.toContain('<');
    expect(out).not.toContain('>');
  });

  it('strips parentheses from node labels', () => {
    const out = san('flowchart TD\nA[Component (2.4GHz)]');
    expect(out).not.toMatch(/\([^)]*GHz[^)]*\)/);
  });

  it('replaces & with "and" inside labels', () => {
    const out = san('flowchart TD\nA[TX & RX]');
    expect(out).toContain('and');
    expect(out).not.toMatch(/&(?!amp|lt|gt|#)/);
  });

  it('strips double-quotes and single-quotes inside labels', () => {
    const out = san('flowchart TD\nA[\'Mixer\']');
    expect(out).not.toContain("'");
  });

  it('replaces dash sequences inside labels (prevent arrow mis-parse)', () => {
    const out = san('flowchart TD\nA[2----4 GHz]');
    // The ---- must have been collapsed so no >=2-dash run remains inside labels
    expect(out).not.toMatch(/\[[^\]]*-{2,}[^\]]*\]/);
  });
});

describe('HTML entity decoding', () => {
  it('decodes &lt; &gt; &amp; &nbsp;', () => {
    const out = san('flowchart TD\nA[a&amp;b]');
    expect(out).toContain('and');
  });

  it('strips raw HTML tags', () => {
    const out = san('flowchart TD\nA[<br/>label]');
    expect(out).not.toContain('<br');
    expect(out).not.toContain('<');
  });
});

describe('unclosed bracket repair', () => {
  it('joins multi-line labels where [ is unclosed', () => {
    const out = san('flowchart TD\nA[Long\nLabel]\nB-->A');
    // After join, the label still ends with ]
    expect(out).toMatch(/A\[[^\]]*Long[^\]]*Label[^\]]*\]/);
  });

  it('auto-closes a dangling [ on a line (LLM forgot ])', () => {
    const out = san('flowchart TD\nA[Dangling\nB-->A');
    expect(out).toContain(']');
  });
});

describe('missing arrow repair', () => {
  it('inserts --> between two bracket-delimited nodes on one line', () => {
    const out = san('flowchart TD\nA[X] B[Y]');
    expect(out).toMatch(/A\[X\]\s*-->\s*B\[Y\]/);
  });

  it('inserts arrows between 3+ bare identifiers on one line', () => {
    const out = san('flowchart TD\nRF IF DSP');
    expect(out).toMatch(/RF\s*-->\s*IF\s*-->\s*DSP/);
  });
});

describe('end-keyword line separation', () => {
  it('puts trailing `end` on its own line', () => {
    const out = san('flowchart TD\nsubgraph S\nA-->B end');
    expect(out).toMatch(/B\n\s*end/);
  });

  it('puts leading `end` + content on separate lines', () => {
    const out = san('flowchart TD\nsubgraph S\nA-->B\nend C-->D');
    expect(out).toMatch(/end\n\s*C/);
  });
});

describe('Unicode glyphs', () => {
  it('replaces Ω with Ohm', () => {
    const out = san('flowchart TD\nA[50Ω]');
    expect(out).toContain('Ohm');
    expect(out).not.toContain('Ω');
  });

  it('replaces µ with u and ° with deg', () => {
    const out = san('flowchart TD\nA[10µs 90°]');
    expect(out).toContain('u');
    expect(out).toContain('deg');
    expect(out).not.toContain('µ');
    expect(out).not.toContain('°');
  });

  it('replaces smart quotes with straight quotes', () => {
    const out = san('flowchart TD\nA[\u201CMixer\u201D]');
    expect(out).not.toContain('\u201C');
    expect(out).not.toContain('\u201D');
  });
});

describe('happy path — a known-good diagram passes through intact', () => {
  it('preserves a well-formed flowchart', () => {
    const input = 'flowchart TD\n    A[LNA] --> B[Mixer]\n    B --> C[ADC]';
    const out = san(input);
    expect(out).toContain('flowchart TD');
    expect(out).toContain('A[LNA]');
    expect(out).toContain('B[Mixer]');
    expect(out).toContain('C[ADC]');
  });
});

describe('idempotence', () => {
  it('running the sanitiser twice produces the same output', () => {
    const input = 'graph TD\nA[x] B[y]\n%%comment\nZ-->A';
    const once = san(input);
    const twice = san(once);
    expect(twice).toEqual(once);
  });
});

describe('round-bracket nodes with nested parens in quoted labels', () => {
  it('S11("VGA (AGC)<br/>HMC624LP4E") renders as a single node', () => {
    // Regression for the 12 GHz receiver diagram that fell back to the
    // "BLOCK DIAGRAM (source)" view because the round-bracket label
    // sanitiser captured only up to the first inner `)`.
    const input = 'flowchart LR\n    S11("VGA (AGC)<br/>HMC624LP4E")';
    const out = san(input);
    // Must produce a single well-formed square-bracket node.
    expect(out).toMatch(/S11\[[^\]]*VGA[^\]]*AGC[^\]]*HMC624LP4E[^\]]*\]/);
    // No leftover floating text after the node (the failure mode was
    // `S11( VGA  AGC) HMC624LP4E")`).
    expect(out).not.toMatch(/S11[^[]*HMC624LP4E"/);
    expect(out).not.toContain('")');
  });

  it('preserves rounded shape when the label has no inner parens', () => {
    // S4("LNA Stage 1<br/>HMC618ALP3E") has no `()` in the label, so we
    // keep the round-edge visual.
    const input = 'flowchart LR\n    S4("LNA Stage 1<br/>HMC618ALP3E")';
    const out = san(input);
    expect(out).toMatch(/S4\([^)]*LNA Stage 1[^)]*HMC618ALP3E[^)]*\)/);
  });

  it('handles the full receiver front-end block diagram', () => {
    // End-to-end: the exact shape the pipeline emits for a 12 GHz Rx.
    const input = [
      'flowchart LR',
      '    %% 12.00 GHz +- 50 MHz',
      '    ANT((Antenna)) --> S1',
      '    S1["N-type Input Connector<br/>N-type IP67 50 ohm"]',
      '    S2["PCB Trace<br/>50Ohm Microstrip (RO4350B)"]',
      '    S11("VGA (AGC)<br/>HMC624LP4E")',
      '    S1 --> S2',
      '    S2 --> S11',
    ].join('\n');
    const out = san(input);
    // Must still start with a valid diagram type.
    expect(out.split('\n')[0].trim()).toBe('flowchart LR');
    // S2 (square brackets) keeps its RO4350B content.
    expect(out).toMatch(/S2\[[^\]]*RO4350B[^\]]*\]/);
    // S11 converts to square brackets and keeps HMC624LP4E.
    expect(out).toMatch(/S11\[[^\]]*HMC624LP4E[^\]]*\]/);
    // Edges preserved.
    expect(out).toMatch(/S1\s*-->\s*S2/);
    expect(out).toMatch(/S2\s*-->\s*S11/);
  });
});
