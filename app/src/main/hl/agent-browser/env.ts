/**
 * agent-browser integration — layout, binary resolution, and child env.
 *
 * agent-browser replaces the vendored `browser-harness-js` CLI as the way
 * agents drive the session's browser view. It is an external native binary
 * (npm/brew/cargo), not a bundled runtime, so nothing here is materialized
 * from `stock/` except the PATH shim.
 *
 * The one contract agent-browser does NOT give us is target addressing: it has
 * no notion of a CDP targetId. It connects to a port, enumerates page targets
 * as `t1..tN` in its own order, and binds the session's daemon to one of them.
 * The app assigns each session a specific WebContentsView, so `bind.ts` has to
 * translate targetId -> tab id, and the shim has to redo that translation
 * whenever the daemon restarts (which silently resets the binding to t1).
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { enrichedPath } from '../engines/pathEnrich';
import type { SpawnContext } from '../engines/types';

/**
 * agent-browser refuses to start when `<socketDir>/<session>.sock` would exceed
 * the platform's Unix socket path limit (103 bytes on macOS — it validates this
 * itself and exits 1). Everything about these names is therefore budgeted for
 * length: a hashed session id instead of the raw UUID, and a short socket dir
 * under tmpdir instead of under userData, whose path already eats most of the
 * budget (`…/Library/Application Support/Browser Use/harness/…`).
 */
export const SOCKET_PATH_LIMIT = 103;

/** Short, stable, collision-resistant name for one app session. */
export function agentBrowserSessionName(sessionId: string): string {
  return `bu${createHash('sha256').update(sessionId).digest('hex').slice(0, 10)}`;
}

/** Directory materialized from `stock/agent-browser-shim/`; goes on child PATH. */
export function shimDir(harnessDir: string): string {
  return path.join(harnessDir, 'agent-browser-shim');
}

/**
 * Shared by every session — isolation comes from the session name, not from
 * per-session directories, because directory depth is exactly what we cannot
 * afford here.
 */
export function socketDir(): string {
  return path.join(os.tmpdir(), 'buab');
}

/** Absolute path agent-browser will bind its Unix socket to. */
export function socketPath(sessionId: string): string {
  return path.join(socketDir(), `${agentBrowserSessionName(sessionId)}.sock`);
}

export function configPath(sessionId: string): string {
  return path.join(socketDir(), `${agentBrowserSessionName(sessionId)}.config.json`);
}

export function shimEnvPath(sessionId: string): string {
  return path.join(socketDir(), `${agentBrowserSessionName(sessionId)}.shim.env`);
}

/** Same content as `shim.env`, in `set "K=V"` form for the `.cmd` shim. */
export function shimCmdPath(sessionId: string): string {
  return path.join(socketDir(), `${agentBrowserSessionName(sessionId)}.shim.cmd`);
}

export function daemonPidCachePath(sessionId: string): string {
  return path.join(socketDir(), `${agentBrowserSessionName(sessionId)}.daemon.pid`);
}

/** Where the agent's user-facing files go; watched by runEngine -> `file_output`. */
export function outputsDir(harnessDir: string, sessionId: string): string {
  return path.join(harnessDir, 'outputs', sessionId);
}

export interface AgentBrowserConfig {
  /** Absolute path to the real agent-browser binary. The shim must never
   *  resolve `agent-browser` by name — its own directory is first on PATH. */
  realBinary: string;
  /** Electron's own executable, run with ELECTRON_RUN_AS_NODE=1 to execute
   *  rebind.mjs. Guaranteed present and version-matched; avoids depending on
   *  a system node/bun being installed. */
  electronPath: string;
  rebindScript: string;
  session: string;
  cdpPort: number;
  targetId: string;
  pidFile: string;
  pidCache: string;
  /** Title planted on the target so the initial bind is unambiguous even when
   *  every session view is still `about:blank`. */
  markerTitle: string;
  /** Last known url/title of the assigned target. Refreshed by bind.ts and by
   *  the navigation watcher; this is what the shim matches on once the agent
   *  has navigated away and the marker is gone. */
  target: { url: string; title: string };
}

export function markerTitleFor(sessionId: string): string {
  return `bu-target-${sessionId.replace(/[^A-Za-z0-9]/g, '').slice(0, 32)}`;
}

// ── binary resolution ───────────────────────────────────────────────────────

const IS_WIN = process.platform === 'win32';
const WIN_EXTS = ['.exe', '.cmd', '.bat'];

function isExecutable(file: string): boolean {
  try {
    const st = fs.statSync(file);
    return st.isFile() && (IS_WIN || (st.mode & 0o111) !== 0);
  } catch {
    return false;
  }
}

/**
 * Find the real agent-browser, skipping `excludeDirs` so we never resolve our
 * own shim. Returns an absolute path or null.
 *
 * Searched over the *enriched* PATH, not the process PATH: a Dock-launched
 * Electron app inherits a minimal PATH with no nvm/Homebrew/volta dirs, and
 * `npm install -g agent-browser` lands in exactly those. Every other CLI in this
 * app is resolved the same way (see engines/pathEnrich.ts).
 */
export function resolveAgentBrowserBinary(env: NodeJS.ProcessEnv = process.env, excludeDirs: string[] = []): string | null {
  const pathKey = IS_WIN && Object.prototype.hasOwnProperty.call(env, 'Path') ? 'Path' : 'PATH';
  const enriched = enrichedPath(env[pathKey], { env });
  const dirs = enriched.split(path.delimiter).filter(Boolean);
  const excluded = new Set(excludeDirs.map((d) => path.resolve(d)));
  for (const dir of dirs) {
    if (excluded.has(path.resolve(dir))) continue;
    const candidates = IS_WIN
      ? WIN_EXTS.map((ext) => path.join(dir, `agent-browser${ext}`))
      : [path.join(dir, 'agent-browser')];
    for (const candidate of candidates) {
      if (isExecutable(candidate)) return candidate;
    }
  }
  return null;
}

// ── child env ───────────────────────────────────────────────────────────────

/**
 * Idle timeout for the per-session daemon. agent-browser defaults to one hour,
 * after which it exits and the next command silently rebinds to t1. Runs are
 * usually shorter than that, but a session can sit idle between turns, so give
 * the daemon a long leash and let the shim catch the cases where it still dies.
 */
const IDLE_TIMEOUT_MS = '86400000';

export function applyAgentBrowserEnv(ctx: SpawnContext, env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const shim = shimDir(ctx.harnessDir);

  // Shim dir goes FIRST so the agent's `agent-browser` resolves to our wrapper.
  const agentSkill = path.join(ctx.harnessDir, 'agent-skill');
  const prefix = [shim, agentSkill].join(path.delimiter);
  const pathKey = IS_WIN && Object.prototype.hasOwnProperty.call(env, 'Path') ? 'Path' : 'PATH';
  env[pathKey] = env[pathKey] ? `${prefix}${path.delimiter}${env[pathKey]}` : prefix;

  // Read by the real binary itself.
  env.AGENT_BROWSER_SESSION = agentBrowserSessionName(ctx.sessionId);
  env.AGENT_BROWSER_CDP = String(ctx.cdpPort);
  env.AGENT_BROWSER_SOCKET_DIR = socketDir();
  env.AGENT_BROWSER_IDLE_TIMEOUT_MS = IDLE_TIMEOUT_MS;
  // Deliberately NOT setting AGENT_BROWSER_SCREENSHOT_DIR: that would route
  // every screenshot into the watched outputs dir and flood the chat with the
  // agent's own visual check-ins. AGENTS.md tells the agent to pass an explicit
  // path under $BU_OUTPUTS_DIR only for screenshots worth showing the user.

  // Read by the agent / AGENTS.md.
  env.BU_SESSION_ID = ctx.sessionId;
  env.BU_TARGET_ID = ctx.targetId;
  env.BU_CDP_PORT = String(ctx.cdpPort);
  env.BU_OUTPUTS_DIR = outputsDir(ctx.harnessDir, ctx.sessionId);

  // Read by the shim.
  env.BU_AGENT_BROWSER_SHIMENV = shimEnvPath(ctx.sessionId);
  env.BU_AGENT_BROWSER_SHIMCMD = shimCmdPath(ctx.sessionId);
  env.BU_AGENT_BROWSER_CONFIG = configPath(ctx.sessionId);
  if (!ctx.agentBrowserBinary) {
    // bindAgentBrowser surfaces a proper user-visible error before we ever get
    // here; this just keeps the shim from exec'ing a half-configured command.
    env.BU_AGENT_BROWSER_MISSING = '1';
  }
  return env;
}

/**
 * Write the files the shim consumes. `*.shim.env` is plain `KEY=value` so the
 * POSIX shim can `.` it without a JSON parser; `*.shim.cmd` is the `set "K=V"`
 * equivalent for Windows; `*.config.json` carries everything rebind.mjs needs.
 */
export function writeShimFiles(sessionId: string, config: AgentBrowserConfig): void {
  fs.mkdirSync(socketDir(), { recursive: true });
  fs.writeFileSync(configPath(sessionId), JSON.stringify(config, null, 2), 'utf-8');

  const pairs: Array<[string, string]> = [
    ['AB_BIN', config.realBinary],
    ['AB_ELECTRON', config.electronPath],
    ['AB_REBIND', config.rebindScript],
    ['AB_PIDFILE', config.pidFile],
    ['AB_CACHE', config.pidCache],
  ];

  const shQuote = (v: string): string => `'${v.replace(/'/g, `'\\''`)}'`;
  const shBody = pairs.map(([k, v]) => `${k}=${shQuote(v)}`).join('\n') + '\n';
  fs.writeFileSync(shimEnvPath(sessionId), shBody, { encoding: 'utf-8', mode: 0o600 });

  const cmdBody = pairs.map(([k, v]) => `set "${k}=${v.replace(/"/g, '')}"`).join('\r\n') + '\r\n';
  fs.writeFileSync(shimCmdPath(sessionId), cmdBody, { encoding: 'utf-8', mode: 0o600 });
}
