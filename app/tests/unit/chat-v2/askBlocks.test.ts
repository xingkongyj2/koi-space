/**
 * Spec for the `ask` fence — text-only questionnaire emitted by agents
 * for disambiguation / requirements-gathering. Closed-fence event is
 * `ask_form` with the parsed AskFormPayload.
 *
 * If a test fails, the bug is in `htmlBlocks.ts`, NOT the test.
 */

import { describe, expect, it } from 'vitest';
import { extractAll, parseAskForm, type ExtractEvent } from '@/renderer/hub/chat-v2/htmlBlocks';

function stream(s: string, n: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < s.length; i += n) out.push(s.slice(i, i + n));
  return out;
}
const stream1 = (s: string) => stream(s, 1);

function run(chunks: string[]): ExtractEvent[] {
  return extractAll(chunks);
}

const SAMPLE = {
  prompt: 'Before I shop, a few quick questions:',
  questions: [
    {
      question: 'What kind of SSD do you want?',
      header: 'Form factor',
      multiSelect: false,
      options: [
        { label: 'M.2 NVMe (internal)', description: 'Fast internal SSD' },
        { label: '2.5" SATA (internal)' },
        { label: 'External / portable USB SSD' },
      ],
    },
    {
      question: 'What capacity?',
      header: 'Capacity',
      multiSelect: false,
      options: [
        { label: '1 TB' },
        { label: '2 TB' },
        { label: '4 TB' },
      ],
    },
  ],
};

const fenced = (body: string): string => '```ask\n' + body + '\n```';

describe('ask fence — extraction', () => {
  it('emits ask_form with parsed payload on a clean block', () => {
    const events = run([fenced(JSON.stringify(SAMPLE))]);
    expect(events).toHaveLength(1);
    const ev = events[0];
    expect(ev.kind).toBe('ask_form');
    if (ev.kind !== 'ask_form') throw new Error('unreachable');
    expect(ev.complete).toBe(true);
    expect(ev.parsed?.prompt).toBe('Before I shop, a few quick questions:');
    expect(ev.parsed?.questions).toHaveLength(2);
    expect(ev.parsed?.questions[0].question).toBe('What kind of SSD do you want?');
    expect(ev.parsed?.questions[0].header).toBe('Form factor');
    expect(ev.parsed?.questions[0].multiSelect).toBe(false);
    expect(ev.parsed?.questions[0].allowOther).toBe(true); // default
    expect(ev.parsed?.questions[0].options).toHaveLength(3);
  });

  it('is invariant under char-by-char chunking', () => {
    const events = run(stream1(fenced(JSON.stringify(SAMPLE))));
    expect(events).toHaveLength(1);
    const ev = events[0];
    if (ev.kind !== 'ask_form') return;
    expect(ev.parsed?.questions).toHaveLength(2);
  });

  it.each([2, 3, 7, 13, 50, 200])('is invariant under chunk size %i', (n) => {
    const events = run(stream(fenced(JSON.stringify(SAMPLE)), n));
    expect(events.filter((e) => e.kind === 'ask_form')).toHaveLength(1);
  });

  it('progressively parses complete questions mid-stream', () => {
    const partial =
      '```ask\n{"prompt":"hi","questions":['
      + '{"question":"Q1?","options":[{"label":"a"},{"label":"b"}]},'
      + '{"question":"Q2?","options":[{"label":"c"';
    const events = run([partial]);
    const e = events[0];
    if (e.kind !== 'ask_form') throw new Error('expected ask_form');
    expect(e.complete).toBe(false);
    expect(e.parsed).not.toBeNull();
    expect(e.parsed?.questions).toHaveLength(1);
    expect(e.parsed?.questions[0].question).toBe('Q1?');
  });

  it('coexists with html + options fences in the same stream', () => {
    const html = '```html\n<div>hi</div>\n```';
    const opts = '```options\n{"options":[{"id":"x","image":"i","title":"t"}]}\n```';
    const ask = fenced(JSON.stringify(SAMPLE));
    const events = run([html + '\n' + opts + '\n' + ask]);
    const kinds = events.filter((e) => e.kind !== 'text' || e.text.trim().length > 0).map((e) => e.kind);
    expect(kinds).toEqual(['html_block', 'option_list', 'ask_form']);
  });
});

describe('parseAskForm — validation', () => {
  it('rejects invalid JSON', () => {
    const { parsed, error } = parseAskForm('not json {');
    expect(parsed).toBeNull();
    expect(error).toMatch(/invalid json/);
  });

  it('rejects when questions field is missing', () => {
    const { parsed, error } = parseAskForm('{"prompt":"hi"}');
    expect(parsed).toBeNull();
    expect(error).toMatch(/questions/);
  });

  it('drops questions without text', () => {
    const { parsed } = parseAskForm(JSON.stringify({
      questions: [
        { question: 'ok?', options: [{ label: 'a' }] },
        { options: [{ label: 'a' }] },              // no question text → dropped
        { question: '', options: [{ label: 'a' }] }, // empty → dropped
      ],
    }));
    expect(parsed?.questions).toHaveLength(1);
    expect(parsed?.questions[0].question).toBe('ok?');
  });

  it('drops questions with zero valid options', () => {
    const { parsed } = parseAskForm(JSON.stringify({
      questions: [
        { question: 'good?', options: [{ label: 'a' }] },
        { question: 'bad?', options: [{ description: 'no label' }] },
      ],
    }));
    expect(parsed?.questions).toHaveLength(1);
    expect(parsed?.questions[0].question).toBe('good?');
  });

  it('rejects when zero questions survive', () => {
    const { parsed, error } = parseAskForm(JSON.stringify({
      questions: [{ question: 'bad?', options: [{}] }],
    }));
    expect(parsed).toBeNull();
    expect(error).toMatch(/no valid questions/);
  });

  it('defaults multiSelect=false and allowOther=true', () => {
    const { parsed } = parseAskForm(JSON.stringify({
      questions: [{ question: 'q?', options: [{ label: 'a' }] }],
    }));
    expect(parsed?.questions[0].multiSelect).toBe(false);
    expect(parsed?.questions[0].allowOther).toBe(true);
  });

  it('honors explicit allowOther=false', () => {
    const { parsed } = parseAskForm(JSON.stringify({
      questions: [{ question: 'q?', allowOther: false, options: [{ label: 'a' }] }],
    }));
    expect(parsed?.questions[0].allowOther).toBe(false);
  });

  it('preserves header + description fields', () => {
    const { parsed } = parseAskForm(JSON.stringify({
      questions: [{
        question: 'q?', header: 'Cap', multiSelect: true,
        options: [{ label: 'a', description: 'x' }, { label: 'b' }],
      }],
    }));
    expect(parsed?.questions[0].header).toBe('Cap');
    expect(parsed?.questions[0].multiSelect).toBe(true);
    expect(parsed?.questions[0].options[0].description).toBe('x');
    expect(parsed?.questions[0].options[1].description).toBeUndefined();
  });
});
