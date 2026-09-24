/**
 * Hard benchmark for the streaming html-block extractor.
 *
 * Goal: regardless of *how* an engine chunks its text deltas, the
 * extractor must produce the same logical sequence of (text, html_block,
 * text, …) events. This file pins down the contract with both
 * synthetic stress tests and engine-flavored fixtures derived from how
 * claude-code, codex, and browsercode/opencode actually emit deltas.
 *
 * Conventions:
 *   - "stream(s, n)" = split `s` into n-character chunks (worst-case
 *      uniform chunking, including splitting mid-fence)
 *   - "stream1(s)"   = split into 1-character chunks (pathological)
 *   - "engineChunks" helpers mimic the realistic shape each engine
 *      emits — see the comments above each fixture for the rationale.
 *
 * If a test fails, the bug is in `htmlBlocks.ts`, NOT the test. Update
 * the spec only if you can explain the new behavior in the comments
 * above the test.
 */

import { describe, expect, it } from 'vitest';
import { HtmlBlockExtractor, extractAll, type ExtractEvent } from '@/renderer/hub/chat-v2/htmlBlocks';

// --- helpers ----------------------------------------------------------------

function stream(s: string, n: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < s.length; i += n) out.push(s.slice(i, i + n));
  return out;
}
const stream1 = (s: string) => stream(s, 1);

/** Drive a fresh extractor across `chunks` and return the merged
 *  events including a final `end()` flush. */
function run(chunks: string[]): ExtractEvent[] {
  return extractAll(chunks);
}

/** Reduce to the kind+content summary for easy assertions. */
function summary(events: ExtractEvent[]): Array<[string, string, boolean?]> {
  return events.map((e) => {
    if (e.kind === 'text') return ['text', e.text];
    if (e.kind === 'html_block') return ['html_block', e.content, e.complete];
    return ['option_list', e.raw, e.complete];
  });
}

const PLAN_HTML = `<div class="plan">
  <h2>Refactor pass</h2>
  <ol>
    <li>Inventory call sites</li>
    <li>Replace one at a time</li>
  </ol>
</div>`;

const fenced = (body: string, tag: 'html' | 'htmlview' = 'html'): string =>
  '```' + tag + '\n' + body + '\n```';

// =============================================================================
// Section 1 — atomic single block
// =============================================================================

describe('htmlBlocks — single block, no surrounding text', () => {
  it('extracts a clean block delivered in one chunk', () => {
    const events = run([fenced(PLAN_HTML)]);
    expect(summary(events)).toEqual([['html_block', PLAN_HTML, true]]);
  });

  it('extracts the same block delivered char-by-char', () => {
    const events = run(stream1(fenced(PLAN_HTML)));
    expect(summary(events)).toEqual([['html_block', PLAN_HTML, true]]);
  });

  it.each([2, 3, 4, 5, 7, 13, 50, 200])(
    'is invariant under chunk size %i',
    (n) => {
      const events = run(stream(fenced(PLAN_HTML), n));
      expect(summary(events)).toEqual([['html_block', PLAN_HTML, true]]);
    },
  );

  it('accepts the htmlview tag variant', () => {
    const events = run([fenced('<p>hi</p>', 'htmlview')]);
    expect(events).toHaveLength(1);
    if (events[0].kind !== 'html_block') throw new Error('expected html_block');
    expect(events[0].tag).toBe('htmlview');
    expect(events[0].content).toBe('<p>hi</p>');
  });
});

// =============================================================================
// Section 2 — text before, after, around blocks
// =============================================================================

describe('htmlBlocks — mixed prose + blocks', () => {
  it('keeps prose before and after a block', () => {
    const input = `Here is a plan:\n\n${fenced(PLAN_HTML)}\n\nThat covers it.`;
    const events = run([input]);
    expect(summary(events)).toEqual([
      ['text', 'Here is a plan:\n\n'],
      ['html_block', PLAN_HTML, true],
      ['text', '\n\nThat covers it.'],
    ]);
  });

  it('handles two blocks separated by prose', () => {
    const a = '<p>first</p>';
    const b = '<p>second</p>';
    const input = `Intro.\n${fenced(a)}\nMiddle.\n${fenced(b)}\nOutro.`;
    const events = run([input]);
    expect(summary(events)).toEqual([
      ['text', 'Intro.\n'],
      ['html_block', a, true],
      ['text', '\nMiddle.\n'],
      ['html_block', b, true],
      ['text', '\nOutro.'],
    ]);
  });

  it('handles two blocks back-to-back with only a newline between', () => {
    const a = '<p>a</p>';
    const b = '<p>b</p>';
    const input = `${fenced(a)}\n${fenced(b)}`;
    const events = run([input]);
    expect(summary(events)).toEqual([
      ['html_block', a, true],
      ['text', '\n'],
      ['html_block', b, true],
    ]);
  });

  it('passes pure prose through untouched (no false-positive fence detection)', () => {
    const prose = 'This response is just markdown text with `inline` code and **bold** but no html block.';
    expect(summary(run([prose]))).toEqual([['text', prose]]);
  });
});

// =============================================================================
// Section 3 — split-fence stress (the real failure mode)
// =============================================================================

describe('htmlBlocks — fence split across chunk boundaries', () => {
  // The opener can be cut at any of: '`', '``', '```', '```h', '```ht',
  // '```htm', '```html', '```html\n'. We test the worst case by
  // streaming 1 char at a time, but also verify a handful of named
  // boundaries deliver the expected sequence.
  const named: Array<{ name: string; chunks: string[] }> = [
    { name: 'opener split at second backtick',  chunks: ['Hi.\n``', '`html\n<p>x</p>\n```'] },
    { name: 'opener split between ``` and html', chunks: ['Hi.\n```', 'html\n<p>x</p>\n```'] },
    { name: 'opener split between html and \\n', chunks: ['Hi.\n```html', '\n<p>x</p>\n```'] },
    { name: 'closer split between fence chars',  chunks: ['Hi.\n```html\n<p>x</p>\n`', '``\n'] },
    { name: 'closer split exactly at newline',   chunks: ['Hi.\n```html\n<p>x</p>\n```', '\n'] },
    { name: 'block content split mid-tag',       chunks: ['```html\n<di', 'v>plan</di', 'v>\n```'] },
  ];

  for (const { name, chunks } of named) {
    it(name, () => {
      const events = run(chunks);
      const lastBlock = events.find((e) => e.kind === 'html_block');
      expect(lastBlock?.kind).toBe('html_block');
    });
  }

  it('is invariant under 1-char chunking for a mixed transcript', () => {
    const big = `Plan time.\n\n${fenced(PLAN_HTML)}\n\nThen we ship.`;
    const events1 = run(stream1(big));
    const eventsAll = run([big]);
    expect(summary(events1)).toEqual(summary(eventsAll));
  });
});

// =============================================================================
// Section 4 — incomplete / never-closed blocks
// =============================================================================

describe('htmlBlocks — incomplete streams', () => {
  it('emits html_block with complete=false when stream ends mid-block', () => {
    const partial = '```html\n<div>still going';
    const events = run([partial]);
    expect(summary(events)).toEqual([['html_block', '<div>still going', false]]);
  });

  it('emits trailing text on stream end (no fence)', () => {
    const events = run(['trailing prose']);
    expect(summary(events)).toEqual([['text', 'trailing prose']]);
  });

  it('emits text-then-partial-block when stream ends mid-block', () => {
    const events = run(['Intro.\n', '```html\n<p>par']);
    expect(summary(events)).toEqual([
      ['text', 'Intro.\n'],
      ['html_block', '<p>par', false],
    ]);
  });

  it('does not lock buffer indefinitely when no closer ever arrives', () => {
    // 100 chunks, no closing fence — extractor must not throw, and end()
    // must surface the accumulated content.
    const ex = new HtmlBlockExtractor();
    const before: ExtractEvent[] = [];
    before.push(...ex.feed('```html\n'));
    for (let i = 0; i < 100; i++) before.push(...ex.feed(`<p>chunk ${i}</p>\n`));
    const after = ex.end();
    const final = [...before, ...after];
    const blocks = final.filter((e) => e.kind === 'html_block');
    expect(blocks).toHaveLength(1);
    if (blocks[0].kind !== 'html_block') throw new Error('expected html_block');
    expect(blocks[0].complete).toBe(false);
    expect(blocks[0].content.length).toBeGreaterThan(1000);
  });
});

// =============================================================================
// Section 5 — adversarial / lookalike content that must NOT be detected
// =============================================================================

describe('htmlBlocks — non-html fences must not match', () => {
  it('leaves a ```js fenced block as inline text (not an html block)', () => {
    const input = '```js\nconsole.log(1);\n```';
    expect(summary(run([input]))).toEqual([['text', input]]);
  });

  it('leaves a bare ``` fenced block (no language) as text', () => {
    const input = '```\nplain\n```';
    expect(summary(run([input]))).toEqual([['text', input]]);
  });

  it('does not match ```html that lacks a following newline', () => {
    // "```html-attributes" — a markdown heading or inline reference.
    const input = 'See ```html-attributes for details.';
    expect(summary(run([input]))).toEqual([['text', input]]);
  });

  it('does not match a fence in the middle of a line', () => {
    // Backticks only open a block when at start of line.
    const input = 'inline ```html stuff``` end';
    expect(summary(run([input]))).toEqual([['text', input]]);
  });
});

// =============================================================================
// Section 6 — engine-flavored fixtures
//
// These mimic how each adapter actually pushes thinking deltas. The
// shapes come from the adapter source — see comments above each fixture.
// =============================================================================

describe('htmlBlocks — engine fixtures', () => {
  // claude-code (src/main/hl/engines/claude-code/adapter.ts:202)
  // Emits one `thinking` HlEvent per Anthropic `text_delta`. Real deltas
  // are usually 1-50 chars, sometimes a single token like "<" or
  // "</div>".
  describe('claude-code: many tiny deltas', () => {
    const claudeChunks = [
      'Here is the ', 'plan ', 'I propose:', '\n\n',
      '```', 'html', '\n',
      '<div ', 'class=', '"plan">\n',
      '  <h2>', 'Migrate', '</h2>\n',
      '  <ul><li>step ', 'one', '</li>',
      '<li>step ', 'two', '</li></ul>\n',
      '</div>',
      '\n', '```',
      '\n\n', 'Let me know if that ', 'looks right.',
    ];
    const expected = [
      ['text', 'Here is the plan I propose:\n\n'],
      ['html_block', '<div class="plan">\n  <h2>Migrate</h2>\n  <ul><li>step one</li><li>step two</li></ul>\n</div>', true],
      ['text', '\n\nLet me know if that looks right.'],
    ];
    it('produces the expected sequence', () => {
      expect(summary(run(claudeChunks))).toEqual(expected);
    });
  });

  // codex (src/main/hl/engines/codex/adapter.ts:230)
  // Emits a single `thinking` per agent_message item — chunks tend to
  // be whole paragraphs rather than tokens. Fences therefore arrive
  // mostly-intact, but the opener and closer can sit at the very edge
  // of a chunk.
  describe('codex: paragraph-sized chunks', () => {
    const codexChunks = [
      'I will lay out the steps as an HTML plan so you can read it at a glance.\n\n',
      '```html\n<ol>\n  <li>Inventory call sites</li>\n  <li>Replace one at a time</li>\n  <li>Run the integration suite</li>\n</ol>\n```\n',
      'Each step is independent — feel free to reorder.',
    ];
    const events = extractAll(codexChunks);
    it('separates prose, block, prose in order', () => {
      expect(events.map((e) => e.kind)).toEqual(['text', 'html_block', 'text']);
    });
    it('captures the full ordered list verbatim', () => {
      const blk = events.find((e) => e.kind === 'html_block');
      if (blk?.kind !== 'html_block') throw new Error('expected html_block');
      expect(blk.content).toContain('Inventory call sites');
      expect(blk.content).toContain('Run the integration suite');
      expect(blk.content.startsWith('<ol>')).toBe(true);
      expect(blk.content.endsWith('</ol>')).toBe(true);
    });
  });

  // browsercode / opencode (src/main/hl/engines/browsercode/adapter.ts:212)
  // Chunks via `textFromPart` reading the `text` field of each part —
  // usually one part = one logical sentence/line. Worst case: a single
  // line that contains both the opener AND closer in the same chunk
  // (because the model emitted a short single-line HTML block).
  describe('browsercode: single-chunk one-liner', () => {
    const bcodeChunks = [
      'Quick comparison:\n',
      '```html\n<table><tr><th>A</th><th>B</th></tr><tr><td>fast</td><td>safe</td></tr></table>\n```\n',
      'Pick safe unless latency is critical.',
    ];
    it('extracts the table cleanly', () => {
      const events = run(bcodeChunks);
      expect(events.map((e) => e.kind)).toEqual(['text', 'html_block', 'text']);
      const blk = events.find((e) => e.kind === 'html_block');
      if (blk?.kind !== 'html_block') throw new Error('expected html_block');
      expect(blk.content).toContain('<table>');
      expect(blk.content).toContain('</table>');
    });
  });
});

// =============================================================================
// Section 7 — invariants
// =============================================================================

describe('htmlBlocks — invariants', () => {
  // Round-trip: text(concatenation of chunks) === text concatenation of
  // (extracted text events) + fenced re-emission of html blocks. This
  // catches "the extractor silently dropped content" regressions.
  it('preserves all input bytes when re-assembled', () => {
    const input = `Plan:\n\n${fenced(PLAN_HTML)}\n\nDetails below.\n\n${fenced('<p>hi</p>')}\n\nEnd.`;
    const events = run(stream(input, 3));
    const reassembled = events.map((e) => {
      if (e.kind === 'text') return e.text;
      if (e.kind === 'html_block') return '```' + e.tag + '\n' + e.content + (e.complete ? '\n```' : '');
      return '```options\n' + e.raw + (e.complete ? '\n```' : '');
    }).join('');
    expect(reassembled).toBe(input);
  });

  it('never emits an empty text event', () => {
    const inputs = [
      fenced(PLAN_HTML),
      `${fenced('<p>a</p>')}${fenced('<p>b</p>')}`,
      'just prose',
    ];
    for (const input of inputs) {
      const events = run(stream(input, 4));
      for (const e of events) {
        if (e.kind === 'text') expect(e.text.length).toBeGreaterThan(0);
      }
    }
  });
});
