/**
 * fromHlEvents — adapt the legacy HlEvent stream into AI-SDK-style
 * UIMessageV2 records.
 *
 * Pure, side-effect-free. Only emits via the chat-v2 logger.
 * Contract is captured in app/tests/unit/chat-v2/fromHlEvents.test.ts —
 * do not change behavior without updating the spec.
 */

import type { HlEvent } from '../types';
import { logger } from './logger';
import type {
  MessagePart,
  ReasoningPart,
  TextPart,
  ToolPart,
  UIMessageV2,
} from './parts';

export function fromHlEvents(
  events: HlEvent[],
  timestamps: number[],
): UIMessageV2[] {
  const messages: UIMessageV2[] = [];
  let current: UIMessageV2 | null = null;
  let idCounter = 0;

  const nextId = (role: 'user' | 'assistant'): string => {
    idCounter += 1;
    return `m-${role}-${idCounter}`;
  };

  const ensureAssistant = (ts: number): UIMessageV2 => {
    if (current && current.role === 'assistant') return current;
    if (current) messages.push(current);
    current = {
      id: nextId('assistant'),
      role: 'assistant',
      parts: [],
      status: 'streaming',
      createdAt: ts,
    };
    return current;
  };

  const closeStreamingReasoning = (msg: UIMessageV2): void => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i];
      if (p.type === 'reasoning' && p.state === 'streaming') {
        const next: ReasoningPart = { ...p, state: 'done' };
        logger.partUpdate(msg.id, i, p, next);
        msg.parts[i] = next;
        return;
      }
      if (p.type !== 'reasoning') return;
    }
  };

  const closeStreamingText = (msg: UIMessageV2): void => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i];
      if (p.type === 'text' && p.state === 'streaming') {
        const next: TextPart = { ...p, state: 'done' };
        logger.partUpdate(msg.id, i, p, next);
        msg.parts[i] = next;
        return;
      }
      if (p.type !== 'text') return;
    }
  };

  /** Locate the most-recent streaming text part in the message, or null. */
  const findStreamingTextIdx = (msg: UIMessageV2): number => {
    for (let i = msg.parts.length - 1; i >= 0; i--) {
      const p = msg.parts[i];
      if (p.type === 'text' && p.state === 'streaming') return i;
      // A tool/file/notify between text deltas means the model paused output
      // for a side-effect — that pause closes the previous text run.
      if (p.type !== 'text') return -1;
    }
    return -1;
  };

  for (let i = 0; i < events.length; i++) {
    const evt = events[i];
    const ts = timestamps[i] ?? 0;
    logger.event(i, evt.type, summarizeEvent(evt));

    switch (evt.type) {
      case 'user_input': {
        if (current) {
          messages.push(current);
          current = null;
        }
        const userMsg: UIMessageV2 = {
          id: nextId('user'),
          role: 'user',
          parts: [{ type: 'text', state: 'done', text: evt.text } satisfies TextPart],
          status: 'done',
          createdAt: ts,
        };
        logger.partAppend(userMsg.id, 0, userMsg.parts[0]);
        messages.push(userMsg);
        break;
      }

      case 'thinking': {
        // NB: the legacy `thinking` HlEvent carries the assistant's streaming
        // *output text* (claude-code `content_block_delta.text_delta`, codex
        // `agent_message`, etc.) — not chain-of-thought. Map it to a `text`
        // part so it renders as the answer, not a collapsible reasoning
        // block. Real reasoning blocks would need a separate HlEvent type.
        const msg = ensureAssistant(ts);
        const streamingIdx = findStreamingTextIdx(msg);
        if (streamingIdx >= 0) {
          const last = msg.parts[streamingIdx] as TextPart;
          const next: TextPart = { ...last, text: last.text + evt.text };
          logger.partUpdate(msg.id, streamingIdx, last, next);
          msg.parts[streamingIdx] = next;
        } else {
          const part: TextPart = {
            type: 'text',
            state: 'streaming',
            text: evt.text,
          };
          msg.parts.push(part);
          logger.partAppend(msg.id, msg.parts.length - 1, part);
        }
        break;
      }

      case 'tool_call': {
        const msg = ensureAssistant(ts);
        closeStreamingReasoning(msg);
        closeStreamingText(msg);
        const callId = `${evt.name}#${evt.iteration}`;
        const part: ToolPart = {
          type: 'tool',
          state: 'input-available',
          toolCallId: callId,
          toolName: evt.name,
          input: evt.args,
          startedAt: ts,
        };
        msg.parts.push(part);
        logger.partAppend(msg.id, msg.parts.length - 1, part);
        logger.toolStart(evt.name, callId, ts);
        break;
      }

      case 'tool_result': {
        const msg = ensureAssistant(ts);
        // Most-recent unfinished tool of this name (FIFO match — engines
        // emit results in call order).
        let idx = -1;
        for (let j = 0; j < msg.parts.length; j++) {
          const p = msg.parts[j];
          if (
            p.type === 'tool'
            && p.toolName === evt.name
            && (p.state === 'input-streaming' || p.state === 'input-available')
          ) {
            idx = j;
            break;
          }
        }
        if (idx < 0) {
          // Orphan result — should be rare. Synthesize a tool part to keep
          // information from being lost.
          const callId = `${evt.name}#orphan-${i}`;
          const part: ToolPart = {
            type: 'tool',
            state: evt.ok ? 'output-available' : 'output-error',
            toolCallId: callId,
            toolName: evt.name,
            startedAt: ts,
            completedAt: ts,
            durationMs: evt.ms,
            output: evt.ok ? evt.preview : undefined,
            errorText: evt.ok ? undefined : evt.preview,
          };
          msg.parts.push(part);
          logger.partAppend(msg.id, msg.parts.length - 1, part);
          logger.toolEnd(evt.name, callId, ts, evt.ms, evt.ok);
          break;
        }
        const prev = msg.parts[idx] as ToolPart;
        const next: ToolPart = {
          ...prev,
          state: evt.ok ? 'output-available' : 'output-error',
          completedAt: ts,
          durationMs: evt.ms,
          output: evt.ok ? evt.preview : undefined,
          errorText: evt.ok ? undefined : evt.preview,
        };
        msg.parts[idx] = next;
        logger.partUpdate(msg.id, idx, prev, next);
        logger.toolEnd(evt.name, prev.toolCallId, ts, evt.ms, evt.ok);
        break;
      }

      case 'done': {
        const msg = ensureAssistant(ts);
        closeStreamingReasoning(msg);
        closeStreamingText(msg);
        // If no text streamed during this turn (e.g. tool-only iteration
        // that wrapped up without a final `text_delta`) and the engine
        // gave us a real summary, surface it so the user isn't left with
        // an empty assistant message. Skip "(done)" placeholder.
        const hasText = msg.parts.some((p) => p.type === 'text');
        const summary = evt.summary?.trim() ?? '';
        if (!hasText && summary && summary !== '(done)') {
          const part: TextPart = { type: 'text', state: 'done', text: summary };
          msg.parts.push(part);
          logger.partAppend(msg.id, msg.parts.length - 1, part);
        }
        msg.status = 'done';
        break;
      }

      case 'error': {
        const msg = ensureAssistant(ts);
        closeStreamingReasoning(msg);
        closeStreamingText(msg);
        const part: TextPart = { type: 'text', state: 'done', text: evt.message };
        msg.parts.push(part);
        logger.partAppend(msg.id, msg.parts.length - 1, part);
        msg.status = 'error';
        break;
      }

      case 'file_output': {
        const msg = ensureAssistant(ts);
        closeStreamingReasoning(msg);
        closeStreamingText(msg);
        const part: MessagePart = {
          type: 'file',
          name: evt.name,
          path: evt.path,
          size: evt.size,
          mime: evt.mime,
        };
        msg.parts.push(part);
        logger.partAppend(msg.id, msg.parts.length - 1, part);
        break;
      }

      case 'notify': {
        const msg = ensureAssistant(ts);
        closeStreamingReasoning(msg);
        closeStreamingText(msg);
        const part: MessagePart = {
          type: 'notify',
          level: evt.level,
          message: evt.message,
        };
        msg.parts.push(part);
        logger.partAppend(msg.id, msg.parts.length - 1, part);
        break;
      }

      // Out-of-scope for the chat-v2 spike. The legacy renderer continues
      // to be the source of truth for skill_*, harness_edited, and
      // turn_usage events; v2 silently ignores them.
      case 'skill_written':
      case 'skill_used':
      case 'harness_edited':
      case 'turn_usage':
        break;
    }
  }

  if (current) messages.push(current);

  logger.transformSummary(messages);
  return messages;
}

function summarizeEvent(evt: HlEvent): string {
  switch (evt.type) {
    case 'thinking': return `${evt.text.slice(0, 40)}${evt.text.length > 40 ? '...' : ''}`;
    case 'tool_call': return `${evt.name}#${evt.iteration}`;
    case 'tool_result': return `${evt.name} ok=${evt.ok} ms=${evt.ms}`;
    case 'user_input': return `${evt.text.slice(0, 40)}${evt.text.length > 40 ? '...' : ''}`;
    case 'done': return `iters=${evt.iterations}`;
    case 'error': return evt.message;
    case 'file_output': return `${evt.name} ${evt.size}b`;
    case 'notify': return `${evt.level}: ${evt.message.slice(0, 40)}`;
    case 'skill_written': return `${evt.domain}/${evt.topic}`;
    case 'skill_used': return `${evt.domain ?? '?'}/${evt.topic}`;
    case 'harness_edited': return `${evt.target}:${evt.action}`;
    case 'turn_usage': return `tok=${evt.inputTokens}/${evt.outputTokens}`;
  }
}
