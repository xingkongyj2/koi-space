/**
 * Verbose chat-v2 logger.
 *
 * Per CLAUDE.md: agents need clear log trails. Every part transition, tool
 * lifecycle event, and reasoning toggle goes through here with descriptive
 * context. Silent unless `localStorage.chatv2_debug === '1'` (or NODE_ENV
 * === 'test' so vitest output stays useful).
 */

import type { MessagePart, UIMessageV2 } from './parts';

const PREFIX = '[chat-v2]';

function enabled(): boolean {
  // Node/test environments: enabled when running under vitest so transform
  // tests can assert on captured logs if needed.
  if (typeof process !== 'undefined' && process.env?.VITEST) return true;
  if (typeof process !== 'undefined' && process.env?.CHATV2_DEBUG === '1') return true;
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage?.getItem('chatv2_debug') === '1';
  } catch {
    return false;
  }
}

function log(...args: unknown[]): void {
  if (!enabled()) return;
  // eslint-disable-next-line no-console
  console.log(PREFIX, ...args);
}

export const logger = {
  enabled,

  /** Called once per incoming HlEvent before transform. */
  event(rawIdx: number, type: string, summary: string): void {
    log(`event#${rawIdx} type=${type}`, summary);
  },

  /** A new part was appended to a message. */
  partAppend(msgId: string, partIdx: number, part: MessagePart): void {
    log(`msg=${msgId} +part[${partIdx}]`, partSummary(part));
  },

  /** An existing part transitioned (e.g. streaming -> done, input-available
   *  -> output-available). */
  partUpdate(
    msgId: string,
    partIdx: number,
    prev: MessagePart,
    next: MessagePart,
  ): void {
    log(
      `msg=${msgId} ~part[${partIdx}]`,
      `${partSummary(prev)}  ->  ${partSummary(next)}`,
    );
  },

  /** Tool started. */
  toolStart(toolName: string, callId: string, startedAt: number): void {
    log(`tool.start name=${toolName} id=${callId} t=${startedAt}`);
  },

  /** Tool finished. ms = engine-reported duration. */
  toolEnd(
    toolName: string,
    callId: string,
    completedAt: number,
    durationMs: number,
    ok: boolean,
  ): void {
    log(
      `tool.end name=${toolName} id=${callId} t=${completedAt} ms=${durationMs} ok=${ok}`,
    );
  },

  /** Renderer-side: user expanded/collapsed a reasoning block. */
  reasoningToggle(msgId: string, partIdx: number, open: boolean): void {
    log(`ui.reasoning msg=${msgId} part[${partIdx}] open=${open}`);
  },

  /** Final transform summary, useful when debugging a whole session. */
  transformSummary(messages: UIMessageV2[]): void {
    if (!enabled()) return;
    log(
      `transform.summary messages=${messages.length}`,
      messages.map((m) => `${m.role}:${m.parts.length}p:${m.status}`).join(' | '),
    );
  },
};

function partSummary(part: MessagePart): string {
  switch (part.type) {
    case 'text':
      return `text(${part.state}, ${part.text.length}ch)`;
    case 'reasoning':
      return `reasoning(${part.state}, ${part.text.length}ch)`;
    case 'tool':
      return `tool(${part.toolName}, ${part.state}${part.durationMs != null ? `, ${part.durationMs}ms` : ''})`;
    case 'file':
      return `file(${part.name}, ${part.size}b)`;
    case 'notify':
      return `notify(${part.level}: ${part.message.slice(0, 40)})`;
  }
}
