import { describe, expect, it } from 'vitest';
import { adaptSession, type AgentSession } from '../../../src/renderer/hub/types';

function session(engine: string): AgentSession {
  return {
    id: 'session-1',
    engine,
    prompt: '打开网站',
    status: 'idle',
    createdAt: 1,
    output: [
      { type: 'user_input', text: '打开网站' },
      { type: 'thinking', text: '{"kind":"planner_plan"}' },
      { type: 'tool_call', name: 'browser.open', args: { value: 'https://example.com' }, iteration: 1 },
      { type: 'tool_result', name: 'browser.open', ok: true, preview: 'opened', ms: 12 },
      { type: 'notify', level: 'info', message: '处理中' },
      { type: 'done', summary: '已打开网站', iterations: 1 },
    ],
  };
}

describe('Python chat transcript', () => {
  it('shows the user request and final result without intermediate events', () => {
    const { entries } = adaptSession(session('python'));
    expect(entries.map((entry) => entry.type)).toEqual(['user_input', 'done']);
    expect(entries[1].content).toBe('已打开网站');
    expect(entries[1].rawIdx).toBe(5);
  });

  it('shows a pending question, but does not duplicate a terminal error', () => {
    const failed = session('python');
    failed.output = [
      { type: 'user_input', text: '继续' },
      { type: 'notify', level: 'blocking', message: '请登录' },
    ];
    expect(adaptSession(failed).entries.map((entry) => entry.type)).toEqual([
      'user_input', 'notify',
    ]);
    failed.output.push(
      { type: 'error', message: '登录失败' },
    );
    expect(adaptSession(failed).entries.map((entry) => entry.type)).toEqual([
      'user_input', 'error',
    ]);
  });

  it('preserves detailed chat output for other engines', () => {
    const { entries } = adaptSession(session('other'));
    expect(entries.map((entry) => entry.type)).toEqual([
      'user_input', 'thinking', 'tool_call', 'notify', 'done',
    ]);
  });
});
