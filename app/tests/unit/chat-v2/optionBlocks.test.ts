/**
 * Spec for the `options` fence — emitted by shopping agents to surface a
 * selectable picker. Shares the extractor / state machine with html
 * blocks (same file). Closed-fence event is `option_list` with parsed
 * JSON normalized into `payload.sections[]`. Legacy single-section
 * payloads (top-level `options`) are wrapped into one unnamed section
 * so the renderer always iterates `sections[]`.
 *
 * If a test fails, the bug is in `htmlBlocks.ts`, NOT the test.
 */

import { describe, expect, it } from 'vitest';
import { extractAll, parseOptionList, type ExtractEvent } from '@/renderer/hub/chat-v2/htmlBlocks';

function stream(s: string, n: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < s.length; i += n) out.push(s.slice(i, i + n));
  return out;
}
const stream1 = (s: string) => stream(s, 1);

function run(chunks: string[]): ExtractEvent[] {
  return extractAll(chunks);
}

// Helper: minimal valid option with required url + site fields
const opt = (id: string, extra?: Record<string, unknown>) => ({
  id,
  image: `https://cdn/${id}.jpg`,
  title: `Option ${id}`,
  url: `https://amazon.com/dp/${id}`,
  site: 'Amazon',
  ...extra,
});

const SAMPLE = {
  prompt: 'Which SSD?',
  multiSelect: false,
  options: [
    { id: 'a1', image: 'https://cdn/x.jpg', title: 'Samsung 990 Pro', price: '$169', url: 'https://amazon.com/dp/a1', site: 'Amazon' },
    { id: 'a2', image: 'https://cdn/y.jpg', title: 'WD Black SN850X', price: '$149', url: 'https://amazon.com/dp/a2', site: 'Amazon' },
  ],
};

const fenced = (body: string): string => '```options\n' + body + '\n```';

describe('options fence — extraction (legacy single-section)', () => {
  it('emits option_list normalized into a single section', () => {
    const events = run([fenced(JSON.stringify(SAMPLE))]);
    expect(events).toHaveLength(1);
    const ev = events[0];
    expect(ev.kind).toBe('option_list');
    if (ev.kind !== 'option_list') throw new Error('unreachable');
    expect(ev.complete).toBe(true);
    expect(ev.parsed?.prompt).toBe('Which SSD?');
    expect(ev.parsed?.sections).toHaveLength(1);
    expect(ev.parsed?.sections[0].options).toHaveLength(2);
    expect(ev.parsed?.sections[0].options[0].id).toBe('a1');
    expect(ev.parsed?.sections[0].label).toBeUndefined();
  });

  it('is invariant under char-by-char chunking', () => {
    const events = run(stream1(fenced(JSON.stringify(SAMPLE))));
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('option_list');
    if (events[0].kind !== 'option_list') return;
    expect(events[0].parsed?.sections[0].options).toHaveLength(2);
  });

  it.each([2, 3, 7, 13, 50, 200])('is invariant under chunk size %i', (n) => {
    const events = run(stream(fenced(JSON.stringify(SAMPLE)), n));
    expect(events.filter((e) => e.kind === 'option_list')).toHaveLength(1);
  });

  it('preserves surrounding text', () => {
    const events = run([
      'Here are some choices:\n' + fenced(JSON.stringify(SAMPLE)) + '\nPick one.',
    ]);
    const kinds = events.map((e) => e.kind);
    expect(kinds).toEqual(['text', 'option_list', 'text']);
  });

  it('coexists with an html block in the same stream', () => {
    const html = '```html\n<div>hi</div>\n```';
    const opts = fenced(JSON.stringify(SAMPLE));
    const events = run([html + '\n' + opts]);
    const meaningful = events.filter((e) => e.kind !== 'text' || e.text.trim().length > 0);
    expect(meaningful.map((e) => e.kind)).toEqual(['html_block', 'option_list']);
  });

  it('emits a partial option_list with complete:false when stream ends mid-block', () => {
    // Option is missing url/site — will be dropped; parsed stays null.
    const partial = '```options\n{"options":[{"id":"a1","image":"x","title":"y"';
    const events = run([partial]);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe('option_list');
    if (events[0].kind !== 'option_list') return;
    expect(events[0].complete).toBe(false);
    expect(events[0].parsed).toBeNull();
  });

  it('progressively parses complete inner objects mid-stream', () => {
    const partial =
      '```options\n{"prompt":"Pick a patty","multiSelect":true,"min":1,"max":2,"options":['
      + '{"id":"a1","image":"https://cdn.example/i1.jpg","title":"Beyond","url":"https://amazon.com/a1","site":"Amazon"},'
      + '{"id":"a2","image":"https://cdn.example/i2.jpg","title":"Beef 85/15","url":"https://amazon.com/a2","site":"Amazon"},'
      + '{"id":"a3","image":"i3"';
    const events = run([partial]);
    expect(events).toHaveLength(1);
    const e = events[0];
    expect(e.kind).toBe('option_list');
    if (e.kind !== 'option_list') return;
    expect(e.complete).toBe(false);
    expect(e.parsed).not.toBeNull();
    expect(e.parsed?.sections).toHaveLength(1);
    expect(e.parsed?.sections[0].options).toHaveLength(2);
    expect(e.parsed?.sections[0].options.map((o) => o.id)).toEqual(['a1', 'a2']);
    expect(e.parsed?.prompt).toBe('Pick a patty');
    expect(e.parsed?.sections[0].multiSelect).toBe(true);
    expect(e.parsed?.sections[0].min).toBe(1);
    expect(e.parsed?.sections[0].max).toBe(2);
  });

  it('clamps negative min while progressively parsing legacy options', () => {
    const partial =
      '```options\n{"multiSelect":true,"min":-2,"max":1,"options":['
      + '{"id":"a1","image":"https://cdn.example/i1.jpg","title":"A","url":"https://a.com/1","site":"Amazon"},'
      + '{"id":"a2","image":"https://cdn.example/i2.jpg"';
    const events = run([partial]);
    const e = events[0];
    expect(e.kind).toBe('option_list');
    if (e.kind !== 'option_list') return;
    expect(e.parsed?.sections[0].min).toBe(0);
    expect(e.parsed?.sections[0].max).toBe(1);
  });

  it('renders parsed cards even when trailing comma and EOF arrive', () => {
    const partial =
      '```options\n{"options":['
      + '{"id":"a1","image":"https://cdn.example/i1.jpg","title":"A","url":"https://a.com/1","site":"Amazon"},'
      + '{"id":"a2","image":"https://cdn.example/i2.jpg","title":"B","url":"https://a.com/2","site":"Amazon"},';
    const events = run([partial]);
    const e = events[0];
    if (e.kind !== 'option_list') throw new Error('expected option_list');
    expect(e.parsed?.sections[0].options).toHaveLength(2);
  });

  it('handles strings containing braces inside option titles', () => {
    const partial =
      '```options\n{"options":['
      + '{"id":"a1","image":"https://cdn.example/i1.jpg","title":"Brand {limited edition}","url":"https://a.com/1","site":"Amazon"},'
      + '{"id":"a2","image":"https://cdn.example/i2.jpg","title":"Plain"';
    const events = run([partial]);
    const e = events[0];
    if (e.kind !== 'option_list') throw new Error('expected option_list');
    expect(e.parsed?.sections[0].options).toHaveLength(1);
    expect(e.parsed?.sections[0].options[0].title).toBe('Brand {limited edition}');
  });
});

describe('options fence — multi-section', () => {
  it('parses a sections array with labeled sub-pickers', () => {
    const payload = {
      prompt: 'Pick burger ingredients',
      sections: [
        {
          label: 'Patty',
          multiSelect: false,
          options: [
            opt('patty-1', { title: 'Beyond' }),
            opt('patty-2', { title: 'Beef 85/15' }),
          ],
        },
        {
          label: 'Bun',
          multiSelect: true, min: 1, max: 2,
          options: [
            opt('bun-1', { title: 'Brioche' }),
            opt('bun-2', { title: 'Sesame' }),
          ],
        },
      ],
    };
    const { parsed } = parseOptionList(JSON.stringify(payload));
    expect(parsed?.prompt).toBe('Pick burger ingredients');
    expect(parsed?.sections).toHaveLength(2);
    expect(parsed?.sections[0].label).toBe('Patty');
    expect(parsed?.sections[0].multiSelect).toBe(false);
    expect(parsed?.sections[0].options).toHaveLength(2);
    expect(parsed?.sections[1].label).toBe('Bun');
    expect(parsed?.sections[1].multiSelect).toBe(true);
    expect(parsed?.sections[1].min).toBe(1);
    expect(parsed?.sections[1].max).toBe(2);
  });

  it('drops sections whose options array yields no valid items', () => {
    const payload = {
      sections: [
        { label: 'Good', options: [opt('g1')] },
        { label: 'Bad', options: [{ id: 'b1' }] },   // all options invalid → drop section
      ],
    };
    const { parsed } = parseOptionList(JSON.stringify(payload));
    expect(parsed?.sections).toHaveLength(1);
    expect(parsed?.sections[0].label).toBe('Good');
  });

  it('rejects the whole block when zero sections survive', () => {
    const { parsed, error } = parseOptionList(JSON.stringify({
      sections: [{ label: 'Bad', options: [{ id: 'x' }] }],
    }));
    expect(parsed).toBeNull();
    expect(error).toMatch(/no valid sections/);
  });

  it('per-section fieldSchema is independent across sections', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      sections: [
        {
          label: 'Patty',
          fieldSchema: ['Price', 'Protein'],
          options: [opt('p1')],
        },
        {
          label: 'Bun',
          fieldSchema: ['Price', 'Calories'],
          options: [opt('b1')],
        },
      ],
    }));
    expect(parsed?.sections[0].fieldSchema).toEqual(['Price', 'Protein']);
    expect(parsed?.sections[1].fieldSchema).toEqual(['Price', 'Calories']);
  });

  it('progressively parses complete section objects mid-stream', () => {
    // First section closes, second section opens but isn't closed.
    const partial =
      '```options\n{"prompt":"Pick ingredients","sections":['
      + '{"label":"Patty","options":[{"id":"p1","image":"https://cdn.example/p1.jpg","title":"Beyond","url":"https://a.com/p1","site":"Amazon"}]},'
      + '{"label":"Bun","options":[{"id":"b1","image":"i"';
    const events = run([partial]);
    const e = events[0];
    if (e.kind !== 'option_list') throw new Error('expected option_list');
    expect(e.complete).toBe(false);
    expect(e.parsed).not.toBeNull();
    expect(e.parsed?.sections).toHaveLength(1);
    expect(e.parsed?.sections[0].label).toBe('Patty');
  });
});

describe('parseOptionList — validation', () => {
  it('rejects invalid JSON', () => {
    const { parsed, error } = parseOptionList('{ this is not json');
    expect(parsed).toBeNull();
    expect(error).toMatch(/invalid json/);
  });

  it('rejects when neither options nor sections present', () => {
    const { parsed, error } = parseOptionList('{"prompt":"hi"}');
    expect(parsed).toBeNull();
    expect(error).toMatch(/options|sections/);
  });

  it('drops options missing url or site', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [
        opt('ok'),
        { id: 'no-url', image: 'https://cdn.example/i.jpg', title: 't', site: 'Amazon' },       // missing url
        { id: 'no-site', image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x.com' }, // missing site
        opt('ok2'),
      ],
    }));
    expect(parsed?.sections[0].options.map((o) => o.id)).toEqual(['ok', 'ok2']);
  });

  it('drops malformed individual options within a section', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [
        opt('ok'),
        { id: 'no-title', image: 'https://cdn.example/i.jpg', url: 'https://x', site: 'Amazon' },
        { image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x', site: 'Amazon' },
        { id: 'no-image', title: 't', url: 'https://x', site: 'Amazon' },
        opt('ok2'),
      ],
    }));
    expect(parsed?.sections[0].options.map((o) => o.id)).toEqual(['ok', 'ok2']);
  });

  it('drops known Airbnb badge art instead of rendering it as a listing photo', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [
        opt('badge', {
          site: 'Airbnb',
          url: 'https://www.airbnb.com/rooms/896630866775094698',
          image: 'https://a0.muscache.com/im/pictures/airbnb-platform-assets/AirbnbPlatformAssets-GuestFavorite/original/4d090f93-f9a5-4f06-95e4-ca737c0d0ab5.png?im_w=720',
        }),
        opt('listing-photo', {
          site: 'Airbnb',
          url: 'https://www.airbnb.com/rooms/1070272052169818710',
          image: 'https://a0.muscache.com/im/pictures/user/User-556869189/original/57d2e836-1238-47a9-ac83-26a0fd8fe99f.jpeg?im_w=720',
        }),
      ],
    }));
    expect(parsed?.sections[0].options.map((o) => o.id)).toEqual(['listing-photo']);
  });

  it('rejects when zero options survive in a legacy payload', () => {
    const { parsed, error } = parseOptionList(JSON.stringify({ options: [{ id: 'x' }] }));
    expect(parsed).toBeNull();
    expect(error).toMatch(/no valid options/);
  });

  it('defaults multiSelect=false, min=1, max=1 when omitted', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [opt('a')],
    }));
    expect(parsed?.sections[0].multiSelect).toBe(false);
    expect(parsed?.sections[0].min).toBe(1);
    expect(parsed?.sections[0].max).toBe(1);
  });

  it('honors multiSelect with min/max bounds', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      multiSelect: true,
      min: 2,
      max: 3,
      options: [opt('a'), opt('b'), opt('c'), opt('d')],
    }));
    expect(parsed?.sections[0].multiSelect).toBe(true);
    expect(parsed?.sections[0].min).toBe(2);
    expect(parsed?.sections[0].max).toBe(3);
  });

  it('clamps max below min to min', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      multiSelect: true,
      min: 3,
      max: 1,
      options: [opt('a'), opt('b'), opt('c')],
    }));
    expect(parsed?.sections[0].min).toBe(3);
    expect(parsed?.sections[0].max).toBe(3);
  });

  it('folds legacy price/subtitle/merchant into fields + description', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [{
        id: 'a', image: 'https://cdn.example/i.jpg', title: 't',
        subtitle: 's', price: '$9', merchant: 'Trader Joes',
        url: 'https://x.com/a', site: 'Amazon',
      }],
    }));
    const o = parsed?.sections[0].options[0];
    expect(o?.description).toBe('s');
    expect(o?.fields?.Price).toBe('$9');
    expect(o?.fields?.Merchant).toBe('Trader Joes');
    expect(o?.url).toBe('https://x.com/a');
    expect(o?.site).toBe('Amazon');
  });

  it('keeps the agent-supplied description over a stale subtitle', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [{ id: 'a', image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x.com', site: 'Amazon',
        description: 'long form', subtitle: 'short stale' }],
    }));
    expect(parsed?.sections[0].options[0].description).toBe('long form');
  });

  it('honors explicit fieldSchema in payload order', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      fieldSchema: ['Price', 'Rating', 'Bedrooms'],
      options: [
        { id: 'a', image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x.com/a', site: 'Amazon', fields: { Price: '$120', Rating: '4.5★', Bedrooms: '2' } },
        { id: 'b', image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x.com/b', site: 'Amazon', fields: { Price: '$200', Bedrooms: '3' } },
      ],
    }));
    expect(parsed?.sections[0].fieldSchema).toEqual(['Price', 'Rating', 'Bedrooms']);
    expect(parsed?.sections[0].options[1].fields?.Bedrooms).toBe('3');
    expect(parsed?.sections[0].options[1].fields?.Rating).toBeUndefined();
  });

  it('leaves fieldSchema undefined when not declared', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [
        { id: 'a', image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x.com/a', site: 'Amazon', fields: { Price: '$1', Rating: '4★' } },
        { id: 'b', image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x.com/b', site: 'Amazon', fields: { Price: '$2' } },
      ],
    }));
    expect(parsed?.sections[0].fieldSchema).toBeUndefined();
  });

  it('rejects non-string field values silently', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [{ id: 'a', image: 'https://cdn.example/i.jpg', title: 't', url: 'https://x.com/a', site: 'Amazon', fields: { Price: '$1', Bad: null } }],
    }));
    expect(parsed?.sections[0].options[0].fields?.Price).toBe('$1');
    expect(parsed?.sections[0].options[0].fields?.Bad).toBeUndefined();
  });

  it('defaults allowOther=false per section', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [opt('a')],
    }));
    expect(parsed?.sections[0].allowOther).toBe(false);
  });

  it('honors explicit allowOther=true', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [opt('a')],
      allowOther: true,
    }));
    expect(parsed?.sections[0].allowOther).toBe(true);
  });

  it('honors explicit allowOther=false', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      options: [opt('a')],
      allowOther: false,
    }));
    expect(parsed?.sections[0].allowOther).toBe(false);
  });

  it('allowOther defaults per-section in multi-section blocks', () => {
    const { parsed } = parseOptionList(JSON.stringify({
      sections: [
        { label: 'A', options: [opt('a')] },
        { label: 'B', allowOther: true, options: [opt('b')] },
      ],
    }));
    expect(parsed?.sections[0].allowOther).toBe(false);
    expect(parsed?.sections[1].allowOther).toBe(true);
  });
});
