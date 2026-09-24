import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll, describe, expect, test } from 'vitest';
import { parseTabList, selectTab } from '../../../src/main/hl/agent-browser/bind';
import {
  SOCKET_PATH_LIMIT,
  agentBrowserSessionName,
  applyAgentBrowserEnv,
  configPath,
  daemonPidCachePath,
  markerTitleFor,
  shimDir,
  shimEnvPath,
  socketDir,
  socketPath,
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

describe('parseTabList', () => {
  /**
   * Regression: the takeover overlay loads a `data:text/html,…` URL into its own
   * WebContentsView, so it shows up as a page target whose url is kilobytes of
   * percent-encoded CSS — full of `{`. Anchoring on the first brace in stdout
   * landed inside that CSS and made the whole listing unparseable, which failed
   * every bind while the overlay existed.
   */
  test('parses a listing that contains a huge percent-encoded data: URL tab', () => {
    const encodedCss = encodeURIComponent(
      'body[data-mode="idle"] { background: #131318; }'.repeat(200),
    );
    const payload = JSON.stringify({
      success: true,
      data: {
        tabs: [
          { active: true, label: null, tabId: 't1', title: 'Takeover', type: 'page', url: `data:text/html;charset=utf-8,${encodedCss}` },
          { active: false, label: null, tabId: 't2', title: 'Example Domain', type: 'page', url: 'https://example.org/' },
        ],
      },
      error: null,
    });

    const tabs = parseTabList(payload);
    expect(tabs).toHaveLength(2);
    expect(selectTab(tabs!, { url: 'https://example.org/', title: 'Example Domain' })).toBe('t2');
  });

  test('tolerates a progress glyph before the JSON envelope', () => {
    const tabs = parseTabList('✓ Done\n{"success":true,"data":{"tabs":[{"tabId":"t1","title":"a","url":"https://a.test","type":"page"}]},"error":null}');
    expect(tabs).toHaveLength(1);
  });

  test('returns null for output that holds no JSON at all', () => {
    expect(parseTabList('agent-browser: command not found')).toBeNull();
  });
});

describe('applyAgentBrowserEnv', () => {
  test('points agent-browser at this session and puts the shim first on PATH', () => {
    const ctx = spawnContext();
    const env = applyAgentBrowserEnv(ctx, { PATH: '/usr/bin:/bin' });

    expect(env.AGENT_BROWSER_SESSION).toBe(agentBrowserSessionName('sess-1234', 51234));
    expect(env.AGENT_BROWSER_CDP).toBe('51234');
    expect(env.AGENT_BROWSER_SOCKET_DIR).toBe(socketDir());
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
});

describe('session naming and socket path budget', () => {
  test('derives a stable, filesystem-safe name from any session id', () => {
    expect(agentBrowserSessionName('a/b c:d', 1)).toBe(agentBrowserSessionName('a/b c:d', 1));
    expect(agentBrowserSessionName('a', 1)).not.toBe(agentBrowserSessionName('b', 1));
    expect(agentBrowserSessionName('a/b c:d', 1)).toMatch(/^bu[0-9a-f]+$/);
  });

  /**
   * The CDP port is chosen at random on every app launch, but agent-browser's
   * daemon caches the endpoint it first connected to and ignores
   * AGENT_BROWSER_CDP afterwards. Folding the port into the session name makes a
   * restart produce a fresh daemon instead of one aimed at a dead port.
   */
  test('changes the session name when the CDP port changes', () => {
    expect(agentBrowserSessionName('sess-1', 51234)).not.toBe(agentBrowserSessionName('sess-1', 60271));
  });

  /**
   * Regression: agent-browser refuses to start when `<socketDir>/<session>.sock`
   * exceeds the platform Unix-socket limit (103 bytes on macOS), and it reports
   * that on stdout with an empty stderr — so the failure used to surface as a
   * blank error message. A UUID session id under `<userData>/harness/…` was 174
   * bytes and broke every run.
   */
  test('keeps the socket path under the platform limit for a real UUID session', () => {
    const sessionId = '62257e30-c753-4153-a8d9-b242cbad2987';
    const bytes = Buffer.byteLength(socketPath(agentBrowserSessionName(sessionId, 60271)));
    expect(bytes).toBeLessThanOrEqual(SOCKET_PATH_LIMIT);
  });

  test('does not put the socket under userData, whose path eats the budget', () => {
    expect(socketDir()).toBe(path.join(os.tmpdir(), 'buab'));
  });
});

describe('writeShimFiles', () => {
  test('writes a sourceable POSIX env file and a JSON config', () => {
    const session = agentBrowserSessionName('shim-test-1', 51234);
    const config: AgentBrowserConfig = {
      realBinary: '/usr/local/bin/agent-browser',
      electronPath: '/Applications/App.app/Contents/MacOS/App',
      rebindScript: '/tmp/agent-browser-shim/rebind.mjs',
      session,
      cdpPort: 51234,
      targetId: 'TARGET-A',
      pidFile: path.join(socketDir(), `${session}.pid`),
      pidCache: daemonPidCachePath(session),
      markerTitle: markerTitleFor('shim-test-1'),
      target: { url: 'about:blank', title: '' },
    };

    writeShimFiles(session, config);

    const sh = fs.readFileSync(shimEnvPath(session), 'utf-8');
    expect(sh).toContain(`AB_BIN='/usr/local/bin/agent-browser'`);
    expect(sh).toContain('AB_PIDFILE=');
    // The shim sources this file, so every line must be a valid assignment.
    for (const line of sh.split('\n').filter(Boolean)) {
      expect(line).toMatch(/^AB_[A-Z]+='.+'$/);
    }

    const onDisk = JSON.parse(fs.readFileSync(configPath(session), 'utf-8')) as AgentBrowserConfig;
    expect(onDisk.targetId).toBe('TARGET-A');
    expect(onDisk.markerTitle).toBe(config.markerTitle);
  });

  test('single-quotes a path containing an apostrophe', () => {
    const session = agentBrowserSessionName('shim-test-apos', 1);
    const config: AgentBrowserConfig = {
      realBinary: "/Users/o'brien/bin/agent-browser",
      electronPath: '/bin/true',
      rebindScript: '/tmp/rebind.mjs',
      session,
      cdpPort: 1,
      targetId: 'T',
      pidFile: '/tmp/p.pid',
      pidCache: '/tmp/p.cache',
      markerTitle: 'm',
      target: { url: '', title: '' },
    };

    writeShimFiles(session, config);
    const sh = fs.readFileSync(shimEnvPath(session), 'utf-8');
    expect(sh).toContain(`AB_BIN='/Users/o'\\''brien/bin/agent-browser'`);
  });
});
