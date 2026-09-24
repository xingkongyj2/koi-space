import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll, describe, expect, test } from 'vitest';
import { selectTab } from '../../../src/main/hl/agent-browser/bind';
import {
  agentBrowserSessionName,
  applyAgentBrowserEnv,
  configPath,
  markerTitleFor,
  shimDir,
  shimEnvPath,
  socketDir,
  writeShimFiles,
  type AgentBrowserConfig,
} from '../../../src/main/hl/agent-browser/env';
import type { SpawnContext } from '../../../src/main/hl/engines/types';

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bu-agent-browser-'));

afterAll(() => {
  fs.rmSync(tmpRoot, { recursive: true, force: true });
});

function tab(tabId: string, url: string, title: string | null, type = 'page') {
  return { tabId, url, title, type };
}

function spawnContext(overrides: Partial<SpawnContext> = {}): SpawnContext {
  return {
    prompt: 'do a thing',
    harnessDir: path.join(tmpRoot, 'harness'),
    sessionId: 'sess-1234',
    targetId: 'TARGET-A',
    cdpPort: 51234,
    agentBrowserBinary: '/usr/local/bin/agent-browser',
    attachmentRefs: [],
    ...overrides,
  };
}

describe('selectTab', () => {
  test('prefers the planted marker title', () => {
    const marker = markerTitleFor('sess-1234');
    const tabs = [
      tab('t1', 'https://example.com', 'Example Domain'),
      tab('t2', 'about:blank', marker),
      tab('t3', 'about:blank', ''),
    ];
    expect(selectTab(tabs, { markerTitle: marker, url: 'about:blank', title: '' })).toBe('t2');
  });

  test('falls back to url+title once the agent has navigated', () => {
    const tabs = [
      tab('t1', 'https://a.test', 'A'),
      tab('t2', 'https://b.test', 'B'),
    ];
    expect(selectTab(tabs, { markerTitle: 'bu-target-gone', url: 'https://b.test', title: 'B' })).toBe('t2');
  });

  test('matches on url alone when the title has drifted', () => {
    const tabs = [tab('t1', 'https://a.test', 'A'), tab('t2', 'https://b.test', 'Stale')];
    expect(selectTab(tabs, { url: 'https://b.test', title: 'B' })).toBe('t2');
  });

  test('refuses to guess when several fresh sessions are all about:blank', () => {
    const tabs = [
      tab('t1', 'about:blank', ''),
      tab('t2', 'about:blank', ''),
    ];
    expect(selectTab(tabs, { url: 'about:blank', title: '' })).toBeNull();
  });

  test('refuses to guess when two tabs share url and title', () => {
    const tabs = [
      tab('t1', 'https://a.test', 'Same'),
      tab('t2', 'https://a.test', 'Same'),
    ];
    expect(selectTab(tabs, { url: 'https://a.test', title: 'Same' })).toBeNull();
  });

  test('returns null when the target is not in the list at all', () => {
    expect(selectTab([tab('t1', 'https://a.test', 'A')], { url: 'https://missing.test' })).toBeNull();
  });

  test('ignores non-page targets', () => {
    const tabs = [
      tab('t1', 'chrome-extension://x', 'Ext', 'background_page'),
      tab('t2', 'https://a.test', 'A'),
    ];
    expect(selectTab(tabs, { url: 'https://a.test', title: 'A' })).toBe('t2');
  });
});

describe('applyAgentBrowserEnv', () => {
  test('points agent-browser at this session and puts the shim first on PATH', () => {
    const ctx = spawnContext();
    const env = applyAgentBrowserEnv(ctx, { PATH: '/usr/bin:/bin' });

    expect(env.AGENT_BROWSER_SESSION).toBe('bu-sess-1234');
    expect(env.AGENT_BROWSER_CDP).toBe('51234');
    expect(env.AGENT_BROWSER_SOCKET_DIR).toBe(socketDir(ctx.harnessDir, ctx.sessionId));
    // A long idle timeout keeps the daemon — and therefore the tab binding —
    // alive across gaps between turns.
    expect(Number(env.AGENT_BROWSER_IDLE_TIMEOUT_MS)).toBeGreaterThan(3_600_000);

    const segments = (env.PATH ?? '').split(path.delimiter);
    expect(segments[0]).toBe(shimDir(ctx.harnessDir));
    expect(segments).toContain('/usr/bin');
  });

  test('leaves BU_OUTPUTS_DIR as the watched dir and does not force a screenshot dir', () => {
    const env = applyAgentBrowserEnv(spawnContext(), {});
    expect(env.BU_OUTPUTS_DIR).toBe(path.join(tmpRoot, 'harness', 'outputs', 'sess-1234'));
    // Setting AGENT_BROWSER_SCREENSHOT_DIR would route the agent's private
    // visual check-ins into the chat as file_output events.
    expect(env.AGENT_BROWSER_SCREENSHOT_DIR).toBeUndefined();
  });

  test('flags a missing binary so the shim fails instead of exec-ing nothing', () => {
    const env = applyAgentBrowserEnv(spawnContext({ agentBrowserBinary: null }), {});
    expect(env.BU_AGENT_BROWSER_MISSING).toBe('1');
  });

  test('sanitises session ids that are not filesystem-safe', () => {
    expect(agentBrowserSessionName('a/b c:d')).toBe('bu-a-b-c-d');
  });
});

describe('writeShimFiles', () => {
  test('writes a sourceable POSIX env file and a JSON config', () => {
    const harness = path.join(tmpRoot, 'shim-harness');
    const config: AgentBrowserConfig = {
      realBinary: "/usr/local/bin/agent-browser",
      electronPath: '/Applications/App.app/Contents/MacOS/App',
      rebindScript: path.join(harness, 'agent-browser-shim', 'rebind.mjs'),
      session: 'bu-sess-1',
      cdpPort: 51234,
      targetId: 'TARGET-A',
      pidFile: path.join(socketDir(harness, 'sess-1'), 'bu-sess-1.pid'),
      pidCache: path.join(socketDir(harness, 'sess-1'), 'daemon.pid'),
      markerTitle: markerTitleFor('sess-1'),
      target: { url: 'about:blank', title: '' },
    };

    writeShimFiles(harness, 'sess-1', config);

    const sh = fs.readFileSync(shimEnvPath(harness, 'sess-1'), 'utf-8');
    expect(sh).toContain(`AB_BIN='/usr/local/bin/agent-browser'`);
    expect(sh).toContain('AB_PIDFILE=');
    // The shim sources this file, so every line must be a valid assignment.
    for (const line of sh.split('\n').filter(Boolean)) {
      expect(line).toMatch(/^AB_[A-Z]+='.+'$/);
    }

    const onDisk = JSON.parse(fs.readFileSync(configPath(harness, 'sess-1'), 'utf-8')) as AgentBrowserConfig;
    expect(onDisk.targetId).toBe('TARGET-A');
    expect(onDisk.markerTitle).toBe(config.markerTitle);
  });

  test('single-quotes a path containing an apostrophe', () => {
    const harness = path.join(tmpRoot, "apos'trophe");
    const config: AgentBrowserConfig = {
      realBinary: "/Users/o'brien/bin/agent-browser",
      electronPath: '/bin/true',
      rebindScript: '/tmp/rebind.mjs',
      session: 'bu-s',
      cdpPort: 1,
      targetId: 'T',
      pidFile: '/tmp/p.pid',
      pidCache: '/tmp/p.cache',
      markerTitle: 'm',
      target: { url: '', title: '' },
    };

    writeShimFiles(harness, 's', config);
    const sh = fs.readFileSync(shimEnvPath(harness, 's'), 'utf-8');
    expect(sh).toContain(`AB_BIN='/Users/o'\\''brien/bin/agent-browser'`);
  });
});
