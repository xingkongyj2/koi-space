import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll, afterEach, describe, expect, test, vi } from 'vitest';
import type { ParseContext, SpawnContext } from '../../../src/main/hl/engines/types';

vi.mock('electron', () => ({
  app: { getAppPath: vi.fn(() => path.join(__dirname, '..', '..', '..')) },
}));

const { resolveAgentDir, resolvePython } = await import('../../../src/main/hl/engines/python/adapter');
const adapter = (await import('../../../src/main/hl/engines/registry')).get('python')!;

const REPO_APP = path.join(__dirname, '..', '..', '..');

const tempDirs: string[] = [];

/** A directory that looks enough like `app/python` for resolveAgentDir to accept it. */
function makeFakeAgentDir(opts: { venv: boolean }): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'koi-agent-dir-'));
  tempDirs.push(dir);
  fs.mkdirSync(path.join(dir, 'koi_agent'), { recursive: true });
  fs.writeFileSync(path.join(dir, 'koi_agent', '__main__.py'), '', 'utf-8');
  if (opts.venv) {
    const bin = path.join(dir, '.venv', 'bin');
    fs.mkdirSync(bin, { recursive: true });
    fs.writeFileSync(path.join(bin, 'python'), '#!/bin/sh\n', { encoding: 'utf-8', mode: 0o755 });
  }
  return dir;
}

function spawnContext(overrides: Partial<SpawnContext> = {}): SpawnContext {
  return {
    prompt: 'open https://example.net and report the title',
    harnessDir: '/tmp/harness',
    sessionId: 'sess-abc',
    targetId: 'TARGET-1',
    cdpPort: 51234,
    attachmentRefs: [],
    ...overrides,
  };
}

function parseContext(): ParseContext {
  return {
    iter: 0,
    pendingTools: new Map(),
    harnessToolsPath: '',
    harnessSkillPath: '/tmp/harness/AGENTS.md',
  };
}

afterEach(() => {
  delete process.env.KOI_AGENT_DIR;
  delete process.env.KOI_AGENT_PYTHON;
});

afterAll(() => {
  for (const dir of tempDirs) fs.rmSync(dir, { recursive: true, force: true });
});

describe('python adapter resolution', () => {
  test('finds the bundled agent package under <app>/python', () => {
    expect(resolveAgentDir()).toBe(path.join(REPO_APP, 'python'));
  });

  test('honours KOI_AGENT_DIR when it really contains the package', () => {
    process.env.KOI_AGENT_DIR = path.join(REPO_APP, 'python');
    expect(resolveAgentDir()).toBe(path.join(REPO_APP, 'python'));
  });

  test('reports null for a KOI_AGENT_DIR that is not an agent dir', () => {
    process.env.KOI_AGENT_DIR = '/tmp';
    expect(resolveAgentDir()).toBeNull();
  });

  test.skipIf(process.platform === 'win32')('prefers a project venv over the system interpreter', () => {
    const dir = makeFakeAgentDir({ venv: true });
    process.env.KOI_AGENT_DIR = dir;
    expect(resolvePython()).toBe(path.join(dir, '.venv', 'bin', 'python'));
  });

  test('falls back to a bare interpreter name when there is no venv', () => {
    const dir = makeFakeAgentDir({ venv: false });
    process.env.KOI_AGENT_DIR = dir;
    // A bare name, not a path: spawnCli resolves it through the enriched PATH,
    // which is what makes a GUI-launched app see Homebrew/pyenv interpreters.
    expect(resolvePython()).toBe(process.platform === 'win32' ? 'python' : 'python3');
  });

  test('prefers an explicit KOI_AGENT_PYTHON', () => {
    process.env.KOI_AGENT_PYTHON = '/opt/py/bin/python3.12';
    expect(resolvePython()).toBe('/opt/py/bin/python3.12');
    expect(adapter.binaryName).toBe('/opt/py/bin/python3.12');
  });

  test('probeInstalled fails with an actionable message when the package is missing', async () => {
    process.env.KOI_AGENT_DIR = '/tmp';
    const probe = await adapter.probeInstalled();
    expect(probe.installed).toBe(false);
    expect(probe.error).toMatch(/KOI_AGENT_DIR/);
  });
});

describe('python adapter spawn contract', () => {
  test('runs the package as a module with unbuffered stdout', () => {
    // -u matters: the protocol is line-based NDJSON and a block-buffered
    // stdout would stall the UI until the process exits.
    expect(adapter.buildSpawnArgs(spawnContext(), '')).toEqual(['-u', '-m', 'koi_agent']);
  });

  test('sends the task as one JSON envelope on stdin', () => {
    const ctx = spawnContext();
    const payload = adapter.getStdinPayload!(ctx, ctx.prompt);
    const parsed = JSON.parse(payload) as Record<string, unknown>;
    expect(parsed).toMatchObject({
      userInput: ctx.prompt,
      sessionId: 'sess-abc',
      browser: { cdpPort: 51234, targetId: 'TARGET-1' },
      outputsDir: '/tmp/harness/outputs/sess-abc',
    });
  });

  test('the envelope carries no agent-browser details — the agent owns those', () => {
    const parsed = JSON.parse(adapter.getStdinPayload!(spawnContext(), 'x')) as Record<string, unknown>;
    // cdpPort + targetId are the whole of the app's browser knowledge. Session
    // naming, socket layout and tab binding are the Python agent's business.
    expect(Object.keys(parsed).sort()).toEqual([
      'browser', 'harnessDir', 'outputsDir', 'resumeSessionId', 'sessionId', 'userInput',
    ]);
    expect(Object.keys(parsed.browser as object).sort()).toEqual(['cdpPort', 'targetId']);
  });

  test('injects PYTHONPATH and an enriched PATH, but no AGENT_BROWSER_* vars', () => {
    const env = adapter.buildEnv(spawnContext(), { PATH: '/usr/bin' });
    expect(env.PYTHONUNBUFFERED).toBe('1');
    expect((env.PYTHONPATH ?? '').split(path.delimiter)).toContain(path.join(REPO_APP, 'python'));
    // The enriched PATH is the one thing the agent needs from us: a
    // GUI-launched Electron has no nvm/Homebrew dirs, which is exactly where
    // `npm install -g agent-browser` puts the binary.
    expect((env.PATH ?? '').split(path.delimiter)).toContain('/usr/bin');
    const leaked = Object.keys(env).filter((k) => k.startsWith('AGENT_BROWSER_'));
    expect(leaked).toEqual([]);
  });
});

describe('python adapter event parsing', () => {
  test('passes a well-formed event straight through', () => {
    const ctx = parseContext();
    const result = adapter.parseLine('{"type":"thinking","text":"hello"}', ctx);
    expect(result.events).toEqual([{ type: 'thinking', text: 'hello' }]);
    expect(result.terminalDone).toBe(false);
  });

  test('marks done as terminal', () => {
    const result = adapter.parseLine('{"type":"done","summary":"ok","iterations":2}', parseContext());
    expect(result.terminalDone).toBe(true);
    expect(result.events[0]).toMatchObject({ type: 'done', summary: 'ok' });
  });

  test('surfaces an error event as terminal', () => {
    const result = adapter.parseLine('{"type":"error","message":"boom"}', parseContext());
    expect(result.terminalError).toBe('boom');
  });

  test('tracks the highest iteration seen from tool_call events', () => {
    const ctx = parseContext();
    adapter.parseLine('{"type":"tool_call","name":"agent-browser","args":{},"iteration":3}', ctx);
    adapter.parseLine('{"type":"tool_call","name":"agent-browser","args":{},"iteration":1}', ctx);
    expect(ctx.iter).toBe(3);
  });

  test('drops malformed JSON without throwing', () => {
    expect(adapter.parseLine('not json at all', parseContext()).events).toEqual([]);
  });

  test('drops events that do not match the HlEvent schema', () => {
    // A missing required field must not reach the DB or the renderer.
    expect(adapter.parseLine('{"type":"thinking"}', parseContext()).events).toEqual([]);
    expect(adapter.parseLine('{"type":"invented","x":1}', parseContext()).events).toEqual([]);
  });

  test('passes through a tool_result carrying the browser output', () => {
    const line = JSON.stringify({
      type: 'tool_result',
      name: 'agent-browser',
      ok: true,
      preview: '✓ Example Domain\n  https://example.net/',
      ms: 1668.3,
    });
    const result = adapter.parseLine(line, parseContext());
    expect(result.events[0]).toMatchObject({ type: 'tool_result', ok: true, ms: 1668.3 });
  });
});
