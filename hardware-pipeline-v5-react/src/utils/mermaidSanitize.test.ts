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
