/**
 * Tests for chat-v2 HlEvent → UIMessageV2 transform.
 *
 * Strict TDD: this file was written BEFORE fromHlEvents.ts. It encodes the
 * contract from reagan_plan_chat_v2.md. Adjust the implementation, not the
 * spec, unless you also update the plan.
 */

import { describe, expect, it } from 'vitest';
import type { HlEvent } from '@/renderer/hub/types';
import { fromHlEvents } from '@/renderer/hub/chat-v2/fromHlEvents';
import type {
  TextPart,
  ToolPart,
  UIMessageV2,
} from '@/renderer/hub/chat-v2/parts';

// Helpers --------------------------------------------------------------------

type Stamped = { e: HlEvent; ts: number };

function run(stamped: Stamped[]): UIMessageV2[] {
  return fromHlEvents(
    stamped.map((s) => s.e),
    stamped.map((s) => s.ts),
  );
}

function userText(text: string, ts = 1000): Stamped {
  return { e: { type: 'user_input', text }, ts };
}
function thinking(text: string, ts: number): Stamped {
  return { e: { type: 'thinking', text }, ts };
}
function toolCall(name: string, args: unknown, iteration: number, ts: number): Stamped {
  return { e: { type: 'tool_call', name, args, iteration }, ts };
}
function toolResult(name: string, ok: boolean, preview: string, ms: number, ts: number): Stamped {
  return { e: { type: 'tool_result', name, ok, preview, ms }, ts };
}
function done(summary: string, iterations: number, ts: number): Stamped {
  return { e: { type: 'done', summary, iterations }, ts };
}

// Tests ----------------------------------------------------------------------

describe('fromHlEvents — empty / trivial', () => {
  it('returns no messages for an empty stream', () => {
    expect(fromHlEvents([], [])).toEqual([]);
  });

  it('derives stable ids for repeated transforms of the same stream', () => {
    const stamped = [
      userText('hello', 100),
      thinking('hi back', 110),
      done('hi back', 1, 120),
    ];
    expect(run(stamped).map((m) => m.id)).toEqual(run(stamped).map((m) => m.id));
  });

  it('creates a user message for a lone user_input', () => {
    const out = run([userText('hello', 100)]);
    expect(out).toHaveLength(1);
    expect(out[0].role).toBe('user');
    expect(out[0].status).toBe('done');
    expect(out[0].createdAt).toBe(100);
    expect(out[0].parts).toEqual([
      { type: 'text', state: 'done', text: 'hello' },
    ]);
  });
});

describe('fromHlEvents — text streaming (thinking events ARE the assistant text)', () => {
  // Note: in this codebase a `thinking` HlEvent is the assistant's streaming
  // output text (`content_block_delta.text_delta` from claude-code, etc.) —
  // NOT chain-of-thought. The transform maps it to a `text` part.
  it('concatenates consecutive thinking events into a single text part', () => {
    const out = run([
      userText('q', 100),
      thinking('The Roman Empire ', 110),
      thinking('was founded in ', 120),
      thinking('27 BC.', 130),
      done('The Roman Empire was founded in 27 BC.', 1, 200),
    ]);
    expect(out).toHaveLength(2);
    const asst = out[1];
    expect(asst.role).toBe('assistant');
    expect(asst.parts).toHaveLength(1);
    const t = asst.parts[0] as TextPart;
    expect(t.type).toBe('text');
    expect(t.state).toBe('done');
    expect(t.text).toBe('The Roman Empire was founded in 27 BC.');
  });

  it('keeps text streaming until a tool_call or done arrives', () => {
    const out = run([
      userText('q', 100),
      thinking('partial...', 110),
    ]);
    const asst = out[1];
    expect(asst.status).toBe('streaming');
    const t = asst.parts[0] as TextPart;
    expect(t.state).toBe('streaming');
  });

  it('closes text to done when a tool_call follows', () => {
    const out = run([
      userText('q', 100),
      thinking('Let me search. ', 110),
      toolCall('search', { q: 'cats' }, 0, 120),
    ]);
    const asst = out[1];
    expect(asst.parts).toHaveLength(2);
    const t0 = asst.parts[0] as TextPart;
    expect(t0.type).toBe('text');
    expect(t0.state).toBe('done');
    expect(asst.parts[1].type).toBe('tool');
  });

  it('starts a fresh text run after a tool call (post-tool prose)', () => {
    const out = run([
      userText('q', 100),
      thinking('Searching first. ', 110),
      toolCall('search', null, 0, 120),
      toolResult('search', true, 'ok', 5, 130),
      thinking('Found it: cats are mammals.', 140),
      done('Found it: cats are mammals.', 1, 200),
    ]);
    const parts = out[1].parts;
    expect(parts.map((p) => p.type)).toEqual(['text', 'tool', 'text']);
    expect((parts[0] as TextPart).text).toBe('Searching first. ');
    expect((parts[2] as TextPart).text).toBe('Found it: cats are mammals.');
    expect((parts[2] as TextPart).state).toBe('done');
  });

  it('falls back to done.summary when no text streamed (tool-only iteration)', () => {
    const out = run([
      userText('q', 100),
      toolCall('search', null, 0, 110),
      toolResult('search', true, 'ok', 5, 120),
      done('Here is the answer.', 1, 200),
    ]);
    const parts = out[1].parts;
    expect(parts.map((p) => p.type)).toEqual(['tool', 'text']);
    expect((parts[1] as TextPart).text).toBe('Here is the answer.');
    expect((parts[1] as TextPart).state).toBe('done');
  });

  it('does not surface the "(done)" placeholder summary', () => {
    const out = run([
      userText('q', 100),
      done('(done)', 1, 200),
    ]);
    const parts = out[1].parts;
    expect(parts).toHaveLength(0);
  });
});

describe('fromHlEvents — tool lifecycle & timing', () => {
  it('pairs tool_call with tool_result and records durationMs', () => {
    const out = run([
      userText('q', 100),
      toolCall('search', { q: 'cats' }, 0, 110),
      toolResult('search', true, 'found 3', 47, 200),
    ]);
    const asst = out[1];
    expect(asst.parts).toHaveLength(1);
    const t = asst.parts[0] as ToolPart;
    expect(t.type).toBe('tool');
    expect(t.state).toBe('output-available');
    expect(t.toolName).toBe('search');
    expect(t.toolCallId).toBe('search#0');
    expect(t.startedAt).toBe(110);
    expect(t.completedAt).toBe(200);
    expect(t.durationMs).toBe(47);
    expect(t.input).toEqual({ q: 'cats' });
    expect(t.output).toBe('found 3');
    expect(t.errorText).toBeUndefined();
  });

  it('marks failed tools as output-error', () => {
    const out = run([
      userText('q', 100),
      toolCall('search', null, 0, 110),
      toolResult('search', false, 'timeout', 5000, 5110),
    ]);
    const t = (out[1].parts[0] as ToolPart);
    expect(t.state).toBe('output-error');
    expect(t.errorText).toBe('timeout');
  });

  it('keeps state=input-available when no result has arrived yet', () => {
    const out = run([
      userText('q', 100),
      toolCall('search', null, 0, 110),
    ]);
    const t = out[1].parts[0] as ToolPart;
    expect(t.state).toBe('input-available');
    expect(t.completedAt).toBeUndefined();
    expect(t.durationMs).toBeUndefined();
  });

  it('distinguishes repeated calls to the same tool by iteration', () => {
    const out = run([
      userText('q', 100),
      toolCall('search', { q: 'a' }, 0, 110),
      toolResult('search', true, 'a-result', 10, 120),
      toolCall('search', { q: 'b' }, 1, 130),
      toolResult('search', true, 'b-result', 20, 150),
    ]);
    const parts = out[1].parts as ToolPart[];
    expect(parts).toHaveLength(2);
    expect(parts[0].toolCallId).toBe('search#0');
    expect(parts[1].toolCallId).toBe('search#1');
    expect(parts[0].output).toBe('a-result');
    expect(parts[1].output).toBe('b-result');
  });

  it('pairs interleaved tool_results to the most-recent unfinished call of that name', () => {
    // call A, call B, result A (FIFO match by name)
    const out = run([
      userText('q', 100),
      toolCall('a', null, 0, 110),
      toolCall('b', null, 0, 115),
      toolResult('a', true, 'aR', 5, 120),
      toolResult('b', true, 'bR', 10, 125),
    ]);
    const parts = out[1].parts as ToolPart[];
    expect(parts[0].toolName).toBe('a');
    expect(parts[0].state).toBe('output-available');
    expect(parts[0].output).toBe('aR');
    expect(parts[1].toolName).toBe('b');
    expect(parts[1].output).toBe('bR');
  });
});

describe('fromHlEvents — terminals', () => {
  it('marks assistant status=done on a done event', () => {
    const out = run([
      userText('q', 100),
      thinking('ok', 110),
      done('finished', 1, 200),
    ]);
    expect(out[1].status).toBe('done');
  });

  it('marks assistant status=error and appends an error text part', () => {
    const out = run([
      userText('q', 100),
      { e: { type: 'error', message: 'boom' }, ts: 200 },
    ]);
    const asst = out[1];
    expect(asst.status).toBe('error');
    const tp = asst.parts.find((p) => p.type === 'text') as TextPart;
    expect(tp?.text).toBe('boom');
    expect(tp?.state).toBe('done');
  });
});

describe('fromHlEvents — multi-turn', () => {
  it('starts a new user/assistant pair on each user_input', () => {
    const out = run([
      userText('q1', 100),
      thinking('thinking 1', 110),
      done('done 1', 1, 150),
      userText('q2', 200),
      thinking('thinking 2', 210),
      done('done 2', 1, 250),
    ]);
    expect(out.map((m) => m.role)).toEqual(['user', 'assistant', 'user', 'assistant']);
    expect(out[1].status).toBe('done');
    expect(out[3].status).toBe('done');
  });

  it('flushes a previous streaming assistant when a new user_input arrives', () => {
    const out = run([
      userText('q1', 100),
      thinking('still going...', 110),
      userText('stop', 200),
    ]);
    // First assistant stays streaming (no done) but doesn't swallow the new
    // user message.
    expect(out.map((m) => m.role)).toEqual(['user', 'assistant', 'user']);
    expect(out[1].status).toBe('streaming');
  });
});

describe('fromHlEvents — file/notify pass-through', () => {
  it('emits file parts', () => {
    const out = run([
      userText('q', 100),
      { e: { type: 'file_output', name: 'r.png', path: '/tmp/r.png', size: 12, mime: 'image/png' }, ts: 200 },
    ]);
    expect(out[1].parts[0]).toEqual({
      type: 'file', name: 'r.png', path: '/tmp/r.png', size: 12, mime: 'image/png',
    });
  });

  it('closes streaming text before appending a file part', () => {
    const out = run([
      userText('q', 100),
      thinking('draft answer', 110),
      { e: { type: 'file_output', name: 'r.png', path: '/tmp/r.png', size: 12, mime: 'image/png' }, ts: 120 },
      done('done', 1, 130),
    ]);
    expect((out[1].parts[0] as TextPart).state).toBe('done');
    expect(out[1].parts[1].type).toBe('file');
    expect(out[1].status).toBe('done');
  });

  it('emits notify parts', () => {
    const out = run([
      userText('q', 100),
      { e: { type: 'notify', message: 'heads up', level: 'info' }, ts: 200 },
    ]);
    expect(out[1].parts[0]).toEqual({
      type: 'notify', level: 'info', message: 'heads up',
    });
  });

  it('closes streaming text before appending a notify part', () => {
    const out = run([
      userText('q', 100),
      thinking('draft answer', 110),
      { e: { type: 'notify', message: 'heads up', level: 'info' }, ts: 120 },
      done('done', 1, 130),
    ]);
    expect((out[1].parts[0] as TextPart).state).toBe('done');
    expect(out[1].parts[1].type).toBe('notify');
    expect(out[1].status).toBe('done');
  });
});
