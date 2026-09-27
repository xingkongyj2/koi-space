/**
 * Python engine adapter — runs `python3 -u <agent>/main.py`.
 *
 * The agent lives in the repository's `agent/` directory and is the app's own backend rather than a
 * third-party CLI: no provider login, no model registry, no installer. The task
 * goes in as one JSON envelope on stdin; HlEvents come back as NDJSON on stdout,
 * already in the shape `shared/session-schemas.ts` expects, so `parseLine` is a
 * validation pass rather than a dialect translation.
 *
 * The app's only browser responsibility is naming the view: it resolves the CDP
 * targetId and passes it with the port in the envelope. agent-browser, tab
 * binding, and rebinding all live in `agent/react/browser.py`. The one
 * thing this adapter must still do for that is hand the child an enriched PATH —
 * a GUI-launched Electron process has no nvm/Homebrew directories on it, and
 * that is where `npm install -g agent-browser` puts the binary.
 */

import fs from 'node:fs';
import path from 'node:path';
import { app } from 'electron';
import { mainLogger } from '../../../logger';
import { enrichedEnv } from '../pathEnrich';
import { runCliCapture } from '../cliSpawn';
import { register } from '../registry';
import { HlEventSchema } from '../../../../shared/session-schemas';
import type {
  EngineAdapter,
  InstallProbe,
  ParseContext,
  ParseResult,
  SpawnContext,
} from '../types';

const ID = 'python';
const ENTRY_FILE = 'main.py';

function isAgentDir(dir: string): boolean {
  try {
    return fs.statSync(path.join(dir, ENTRY_FILE)).isFile();
  } catch {
    return false;
  }
}

/**
 * Directory holding main.py and the Planner/ReAct/Reflection/Memory modules.
 *
 * Development uses `<repo>/agent`; Forge copies the runtime package to
 * `<resources>/agent` for packaged builds. KOI_AGENT_DIR can override either.
 */
export function resolveAgentDir(): string | null {
  const override = process.env.KOI_AGENT_DIR;
  if (override) return isAgentDir(override) ? override : null;

  const candidates: string[] = [];
  if (!app.isPackaged) {
    try {
      const appPath = app.getAppPath();
      candidates.push(path.resolve(appPath, '..', 'agent'));
      candidates.push(path.join(appPath, 'agent'));
    } catch { /* app not ready yet */ }
  }
  if (process.resourcesPath) candidates.push(path.join(process.resourcesPath, 'agent'));

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
        error: `Could not find the Koi agent main.py entry. Expected it under <repo>/agent or <resources>/agent, or set KOI_AGENT_DIR.`,
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

  buildSpawnArgs(): string[] {
    // -u: the protocol is line-based NDJSON, so stdout must not be block-buffered.
    const agentDir = resolveAgentDir();
    if (!agentDir) throw new Error('Could not find the Koi agent main.py entry.');
    return ['-u', path.resolve(agentDir, ENTRY_FILE)];
  },

  /** The whole task travels over stdin — argv would need shell quoting for
   *  multi-line prompts and buys nothing here. `browser` is the complete extent
   *  of what the app tells the agent about the browser. */
  getStdinPayload(ctx: SpawnContext): string {
    return JSON.stringify({
      userInput: ctx.prompt,
      history: ctx.history ?? [],
      sessionId: ctx.sessionId,
      browser: { cdpPort: ctx.cdpPort, targetId: ctx.targetId },
      outputsDir: path.join(ctx.harnessDir, 'outputs', ctx.sessionId),
      harnessDir: ctx.harnessDir,
      resumeSessionId: ctx.resumeSessionId ?? null,
    });
  },

  wrapPrompt(ctx: SpawnContext): string {
    return ctx.prompt;
  },

  buildEnv(ctx: SpawnContext, baseEnv: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
    const env = enrichedEnv(baseEnv);
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
