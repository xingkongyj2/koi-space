/**
 * Python engine adapter — wraps `python3 -m koi_agent`.
 *
 * The agent lives in `app/python/` and is the app's own backend rather than a
 * third-party CLI: no provider login, no model registry, no installer. The task
 * goes in as one JSON envelope on stdin; HlEvents come back as NDJSON on stdout,
 * already in the shape `shared/session-schemas.ts` expects, so `parseLine` is a
 * validation pass rather than a dialect translation.
 *
 * Browser control is not this adapter's business. runEngine binds agent-browser
 * to the session's view before spawning and puts its shim first on PATH, so the
 * Python side runs plain `agent-browser <cmd>` and inherits the binding.
 */

import fs from 'node:fs';
import path from 'node:path';
import { app } from 'electron';
import { mainLogger } from '../../../logger';
import { agentBrowserSessionName, applyAgentBrowserEnv } from '../../agent-browser/env';
import { enrichedEnv } from '../pathEnrich';
import { runCliCapture } from '../cliSpawn';
import { register } from '../registry';
import { HlEventSchema } from '../../../../shared/session-schemas';
import type {
  AuthProbe,
  EngineAdapter,
  InstallProbe,
  ParseContext,
  ParseResult,
  SpawnContext,
} from '../types';

const ID = 'python';
const MODULE = 'koi_agent';
const ENTRY_RELATIVE = ['koi_agent', '__main__.py'];

function isAgentDir(dir: string): boolean {
  try {
    return fs.statSync(path.join(dir, ...ENTRY_RELATIVE)).isFile();
  } catch {
    return false;
  }
}

/**
 * Directory holding the `koi_agent` package.
 *
 * In dev this is `<repo>/app/python`. Packaged builds need the same tree copied
 * next to the app resources; until forge config does that, a packaged app falls
 * back to KOI_AGENT_DIR.
 */
export function resolveAgentDir(): string | null {
  const override = process.env.KOI_AGENT_DIR;
  if (override) return isAgentDir(override) ? override : null;

  const candidates: string[] = [];
  try {
    candidates.push(path.join(app.getAppPath(), 'python'));
  } catch { /* app not ready yet */ }
  if (process.resourcesPath) candidates.push(path.join(process.resourcesPath, 'python'));

  for (const candidate of candidates) {
    if (isAgentDir(candidate)) return candidate;
  }
  return null;
}

/**
 * Interpreter to run. A project venv wins so the agent's dependencies never
 * depend on what the system Python happens to have; otherwise fall back to a
 * bare name and let spawnCli resolve it through the enriched PATH.
 */
export function resolvePython(): string {
  const override = process.env.KOI_AGENT_PYTHON;
  if (override) return override;

  const agentDir = resolveAgentDir();
  if (agentDir) {
    const rel = process.platform === 'win32'
      ? ['.venv', 'Scripts', 'python.exe']
      : ['.venv', 'bin', 'python'];
    const venv = path.join(agentDir, ...rel);
    try {
      if (fs.statSync(venv).isFile()) return venv;
    } catch { /* no venv — use the system interpreter */ }
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

const pythonAdapter: EngineAdapter = {
  id: ID,
  displayName: 'Koi Python Agent',
  get binaryName(): string {
    return resolvePython();
  },

  async probeInstalled(): Promise<InstallProbe> {
    const agentDir = resolveAgentDir();
    if (!agentDir) {
      return {
        installed: false,
        error: `Could not find the ${MODULE} package. Expected it under <app>/python, or set KOI_AGENT_DIR.`,
      };
    }
    const python = resolvePython();
    const r = await runCliCapture(python, ['--version']);
    if (!r.ok) {
      return {
        installed: false,
        error: `No usable Python interpreter (${python}): ${r.stderr || r.error || 'failed to run'}. Install Python 3.10+ or set KOI_AGENT_PYTHON.`,
      };
    }
    return { installed: true, version: (r.stdout || r.stderr).trim().replace(/^Python\s*/i, '') };
  },

  async probeAuthed(): Promise<AuthProbe> {
    // The Python agent is the app's own backend. Whatever model access it grows
    // into is configured inside app/python, not through a provider login here.
    return { authed: true };
  },

  async openLoginInTerminal(): Promise<{ opened: boolean; error?: string }> {
    return { opened: false, error: 'The Python agent needs no provider login.' };
  },

  buildSpawnArgs(): string[] {
    // -u: the protocol is line-based NDJSON, so stdout must not be block-buffered.
    return ['-u', '-m', MODULE];
  },

  /** The whole task travels over stdin — argv would need shell quoting for
   *  multi-line prompts and buys nothing here. */
  getStdinPayload(ctx: SpawnContext): string {
    return JSON.stringify({
      prompt: ctx.prompt,
      sessionId: ctx.sessionId,
      targetId: ctx.targetId,
      cdpPort: ctx.cdpPort,
      agentBrowserSession: agentBrowserSessionName(ctx.sessionId, ctx.cdpPort),
      outputsDir: path.join(ctx.harnessDir, 'outputs', ctx.sessionId),
      harnessDir: ctx.harnessDir,
      resumeSessionId: ctx.resumeSessionId ?? null,
    });
  },

  wrapPrompt(ctx: SpawnContext): string {
    return ctx.prompt;
  },

  buildEnv(ctx: SpawnContext, baseEnv: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
    const env = applyAgentBrowserEnv(ctx, enrichedEnv(baseEnv));
    const agentDir = resolveAgentDir();
    if (agentDir) {
      env.PYTHONPATH = env.PYTHONPATH ? `${agentDir}${path.delimiter}${env.PYTHONPATH}` : agentDir;
    }
    env.PYTHONUNBUFFERED = '1';
    return env;
  },

  parseLine(line: string, ctx: ParseContext): ParseResult {
    let raw: unknown;
    try {
      raw = JSON.parse(line);
    } catch {
      return { events: [] };
    }

    const parsed = HlEventSchema.safeParse(raw);
    if (!parsed.success) {
      mainLogger.warn('python.adapter.parse.rejected', {
        line: line.slice(0, 200),
        issue: parsed.error.issues[0]?.message ?? 'invalid event',
      });
      return { events: [] };
    }

    const event = parsed.data;
    if (event.type === 'tool_call') ctx.iter = Math.max(ctx.iter, event.iteration);

    return {
      events: [event],
      terminalDone: event.type === 'done',
      terminalError: event.type === 'error' ? event.message : undefined,
    };
  },
};

register(pythonAdapter);
