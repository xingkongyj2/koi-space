/**
 * Bind an app session's browser view to an agent-browser session.
 *
 * The problem this solves: agent-browser addresses pages as `t1..tN` in its own
 * enumeration order and offers no way to name a CDP targetId. Feeding it a
 * page-level `ws://.../devtools/page/<id>` URL is accepted and then ignored —
 * it still binds whichever tab it considers active. With up to 10 concurrent
 * WebContentsViews sharing one `--remote-debugging-port`, "whichever tab it
 * picks" is usually somebody else's session.
 *
 * So we translate: plant a unique `document.title` on the assigned target,
 * ask agent-browser for its tab list, match on that title, and switch to the
 * tab id. Binding is sticky for the life of the daemon, and the marker is
 * naturally replaced the moment the agent navigates anywhere.
 */

import fs from 'node:fs';
import path from 'node:path';
import type { WebContents } from 'electron';
import { engineLogger } from '../../logger';
import { runCliCapture } from '../engines/cliSpawn';
import {
  type AgentBrowserConfig,
  agentBrowserSessionName,
  configPath,
  daemonPidCachePath,
  markerTitleFor,
  resolveAgentBrowserBinary,
  shimDir,
  socketDir,
  writeShimFiles,
} from './env';

/** The subset of a run that binding needs; kept off SpawnContext so binding can
 *  happen before the context (and therefore the resolved binary) exists. */
export interface BindParams {
  harnessDir: string;
  sessionId: string;
  targetId: string;
  cdpPort: number;
}

export interface BindResult {
  ok: boolean;
  /** Absolute path to the real binary, or null when agent-browser is not installed. */
  binaryPath: string | null;
  /** Tab id agent-browser bound to (e.g. `t3`). */
  tabId?: string;
  error?: string;
}

interface TabEntry {
  tabId: string;
  title: string | null;
  url: string;
  active?: boolean;
  type?: string;
}

function parseTabList(stdout: string): TabEntry[] | null {
  // agent-browser may prefix progress glyphs; anchor on the first brace.
  const start = stdout.indexOf('{');
  if (start < 0) return null;
  try {
    const parsed = JSON.parse(stdout.slice(start)) as { data?: { tabs?: TabEntry[] } };
    const tabs = parsed?.data?.tabs;
    return Array.isArray(tabs) ? tabs : null;
  } catch {
    return null;
  }
}

/**
 * Pick the tab that corresponds to our assigned target.
 * Marker title wins (unambiguous by construction); url+title is the fallback
 * used by the shim after the agent has navigated and the marker is gone.
 * Returns null rather than guessing when the match is not unique — driving the
 * wrong page is worse than failing loudly.
 */
export function selectTab(tabs: TabEntry[], target: { markerTitle?: string; url?: string; title?: string }): string | null {
  const pages = tabs.filter((t) => (t.type ?? 'page') === 'page');
  if (target.markerTitle) {
    const hit = pages.filter((t) => t.title === target.markerTitle);
    if (hit.length === 1) return hit[0].tabId;
  }
  if (target.url) {
    const exact = pages.filter((t) => t.url === target.url && (target.title == null || t.title === target.title));
    if (exact.length === 1) return exact[0].tabId;
    const byUrl = pages.filter((t) => t.url === target.url);
    if (byUrl.length === 1) return byUrl[0].tabId;
  }
  return null;
}

function childEnv(ctx: BindParams, baseEnv: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const sockDir = socketDir(ctx.harnessDir, ctx.sessionId);
  fs.mkdirSync(sockDir, { recursive: true });
  return {
    ...baseEnv,
    AGENT_BROWSER_SESSION: agentBrowserSessionName(ctx.sessionId),
    AGENT_BROWSER_CDP: String(ctx.cdpPort),
    AGENT_BROWSER_SOCKET_DIR: sockDir,
  };
}

function readPidFile(file: string): string {
  try { return fs.readFileSync(file, 'utf-8').trim(); } catch { return ''; }
}

/**
 * Ensure agent-browser's daemon for this session is pointed at `ctx.targetId`.
 * Called from runEngine before spawning the agent, so a missing binary or an
 * unmatchable target becomes a user-visible error instead of an agent quietly
 * automating the wrong page.
 */
export async function bindAgentBrowser(ctx: BindParams, webContents: WebContents): Promise<BindResult> {
  const binaryPath = resolveAgentBrowserBinary(process.env, [shimDir(ctx.harnessDir)]);
  if (!binaryPath) {
    return {
      ok: false,
      binaryPath: null,
      error: 'agent-browser is not installed. Run `npm install -g agent-browser` (or `brew install agent-browser`), then retry.',
    };
  }

  const marker = markerTitleFor(ctx.sessionId);
  const env = childEnv(ctx, process.env);
  const pidFile = path.join(env.AGENT_BROWSER_SOCKET_DIR as string, `${env.AGENT_BROWSER_SESSION}.pid`);

  // Plant the marker. Fails harmlessly on chrome:// or a crashed renderer —
  // we fall back to url matching below.
  let planted = false;
  try {
    await webContents.executeJavaScript(`document.title = ${JSON.stringify(marker)}`, true);
    planted = true;
  } catch (err) {
    engineLogger.warn('agentBrowser.bind.marker.failed', {
      sessionId: ctx.sessionId,
      error: (err as Error).message,
    });
  }

  const listing = await runCliCapture(binaryPath, ['tab', 'list', '--json'], 30_000, { env, cwd: ctx.harnessDir });
  if (!listing.ok) {
    return {
      ok: false,
      binaryPath,
      error: `agent-browser could not reach the browser on CDP port ${ctx.cdpPort}: ${(listing.error ?? listing.stderr).trim().slice(0, 400)}`,
    };
  }
  const tabs = parseTabList(listing.stdout);
  if (!tabs) {
    return { ok: false, binaryPath, error: `Unparseable output from \`agent-browser tab list --json\`: ${listing.stdout.slice(0, 200)}` };
  }

  const targetUrl = safeUrl(webContents);
  const tabId = selectTab(tabs, {
    markerTitle: planted ? marker : undefined,
    url: targetUrl,
    title: safeTitle(webContents),
  });
  if (!tabId) {
    return {
      ok: false,
      binaryPath,
      error: `Could not uniquely identify this session's browser tab among ${tabs.length} candidates (url=${targetUrl}). Refusing to guess — driving the wrong tab would act on another session.`,
    };
  }

  const switched = await runCliCapture(binaryPath, ['tab', tabId], 15_000, { env, cwd: ctx.harnessDir });
  if (!switched.ok) {
    return { ok: false, binaryPath, error: `agent-browser failed to switch to ${tabId}: ${(switched.error ?? switched.stderr).trim().slice(0, 300)}` };
  }

  const config: AgentBrowserConfig = {
    realBinary: binaryPath,
    electronPath: process.execPath,
    rebindScript: path.join(shimDir(ctx.harnessDir), 'rebind.mjs'),
    session: env.AGENT_BROWSER_SESSION as string,
    cdpPort: ctx.cdpPort,
    targetId: ctx.targetId,
    pidFile,
    pidCache: daemonPidCachePath(ctx.harnessDir, ctx.sessionId),
    markerTitle: marker,
    target: { url: targetUrl, title: safeTitle(webContents) },
  };
  writeShimFiles(ctx.harnessDir, ctx.sessionId, config);
  // Seed the pid cache so the agent's first command takes the shim's fast path.
  try { fs.writeFileSync(config.pidCache, readPidFile(pidFile), 'utf-8'); } catch { /* shim will rebind */ }

  engineLogger.info('agentBrowser.bind.ok', {
    sessionId: ctx.sessionId,
    targetId: ctx.targetId,
    tabId,
    markerPlanted: planted,
    candidates: tabs.length,
  });
  return { ok: true, binaryPath, tabId };
}

function safeUrl(wc: WebContents): string {
  try { return wc.getURL(); } catch { return ''; }
}

function safeTitle(wc: WebContents): string {
  try { return wc.getTitle(); } catch { return ''; }
}

/**
 * Keep `shim-config.json`'s target fingerprint current for as long as a run is
 * active. If the daemon dies mid-run the shim re-resolves the tab from this
 * data, so a stale fingerprint would mean a loud failure instead of a rebind.
 * Returns a disposer; runEngine calls it when the child exits.
 */
export function watchTargetFingerprint(ctx: BindParams, webContents: WebContents): () => void {
  const file = configPath(ctx.harnessDir, ctx.sessionId);
  let timer: ReturnType<typeof setTimeout> | null = null;

  const refresh = (): void => {
    // Coalesce bursts (did-start-navigation + did-navigate + title updates).
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      try {
        const config = JSON.parse(fs.readFileSync(file, 'utf-8')) as AgentBrowserConfig;
        config.target = { url: safeUrl(webContents), title: safeTitle(webContents) };
        fs.writeFileSync(file, JSON.stringify(config, null, 2), 'utf-8');
      } catch {
        // No config yet (bind failed) or the view is gone — nothing to refresh.
      }
    }, 150);
  };

  const events: Array<[string, (...args: unknown[]) => void]> = [
    ['did-navigate', refresh],
    ['did-navigate-in-page', refresh],
    ['page-title-updated', refresh],
  ];
  for (const [name, handler] of events) {
    try { webContents.on(name as never, handler as never); } catch { /* view already destroyed */ }
  }
  return () => {
    if (timer) clearTimeout(timer);
    for (const [name, handler] of events) {
      try { webContents.off(name as never, handler as never); } catch { /* already gone */ }
    }
  };
}
