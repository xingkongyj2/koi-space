/**
 * Structured renderer logger.
 *
 * Mirrors the existing `[Module] action.subaction` convention used across
 * the renderer but adds:
 *   - levels (debug / info / warn / error)
 *   - structured extra-fields object
 *   - on-disk persistence by forwarding to the main-process `renderer`
 *     channel logger (via the `renderer:log` IPC channel), so production
 *     errors land in ~/Library/Application Support/Browser Use/logs/
 *     renderer.log instead of evaporating with DevTools.
 *
 * Usage:
 *
 *   const log = makeLogger('EnginePicker');
 *   log.info('refreshStatus.failed', { id, error });
 *   log.error('engineInstall failed', err);
 *
 * The console output stays human-readable; the IPC forward sends a
 * structured `{level, ns, msg, extra}` payload to main, which the
 * ChannelLogger writes as JSONL.
 *
 * Levels filtered to forward to main:
 *   debug: console only (always)
 *   info:  console + main (when localStorage.rendererLog === 'verbose')
 *   warn:  console + main (always)
 *   error: console + main (always)
 *
 * Set localStorage.rendererLog = 'silent' to mute console output entirely
 * (forwarding to main is unaffected so errors still hit disk).
 */

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface Logger {
  debug: (msg: string, extra?: unknown) => void;
  info: (msg: string, extra?: unknown) => void;
  warn: (msg: string, extra?: unknown) => void;
  error: (msg: string, extra?: unknown) => void;
}

const CONSOLE_FNS: Record<LogLevel, (...args: unknown[]) => void> = {
  debug: (...a) => console.debug(...a),
  info: (...a) => console.log(...a),
  warn: (...a) => console.warn(...a),
  error: (...a) => console.error(...a),
};

type Mode = 'verbose' | 'normal' | 'silent';

function readMode(): Mode {
  if (typeof window === 'undefined') return 'normal';
  try {
    const v = window.localStorage?.getItem('rendererLog');
    if (v === 'verbose' || v === 'silent') return v;
  } catch {
    // localStorage can throw in some contexts (private mode, blocked).
    // Fall through to default.
  }
  return 'normal';
}

/** Normalize an "extra" arg into a plain serializable object. Accepts
 *  Errors, primitives, or plain objects to keep call sites ergonomic. */
function normalizeExtra(extra: unknown): Record<string, unknown> | undefined {
  if (extra == null) return undefined;
  if (extra instanceof Error) {
    return { error: extra.message, stack: extra.stack, name: extra.name };
  }
  if (typeof extra === 'object') {
    // Best-effort shallow clone so the main side doesn't receive
    // non-cloneable values (DOM nodes, functions, etc.) that would
    // crash the IPC bridge.
    try {
      return JSON.parse(JSON.stringify(extra)) as Record<string, unknown>;
    } catch {
      return { extra: String(extra) };
    }
  }
  return { value: String(extra) };
}

/** Push a log line to the main process. Best-effort — failures (preload
 *  not loaded, structured-clone errors, etc.) are swallowed because the
 *  console output is the primary channel and logging should never crash
 *  the renderer. */
function forwardToMain(level: LogLevel, ns: string, msg: string, extra?: Record<string, unknown>): void {
  if (typeof window === 'undefined') return;
  const w = window as unknown as { electronAPI?: { log?: (level: LogLevel, ns: string, msg: string, extra?: Record<string, unknown>) => void } };
  try {
    w.electronAPI?.log?.(level, ns, msg, extra);
  } catch {
    // never throw out of a logger
  }
}

function shouldForward(level: LogLevel, mode: Mode): boolean {
  if (level === 'warn' || level === 'error') return true;
  if (level === 'info') return mode === 'verbose';
  // 'debug' never forwards — keeps the disk log signal-rich.
  return false;
}

export function makeLogger(namespace: string): Logger {
  // Bind the namespace once. Mode is re-read on each call so toggling
  // localStorage takes effect without a reload.
  const emit = (level: LogLevel, msg: string, extra?: unknown): void => {
    const mode = readMode();
    const normalized = normalizeExtra(extra);
    if (mode !== 'silent') {
      const prefix = `[${namespace}]`;
      if (normalized !== undefined) CONSOLE_FNS[level](prefix, msg, normalized);
      else CONSOLE_FNS[level](prefix, msg);
    }
    if (shouldForward(level, mode)) {
      forwardToMain(level, namespace, msg, normalized);
    }
  };

  return {
    debug: (msg, extra) => emit('debug', msg, extra),
    info: (msg, extra) => emit('info', msg, extra),
    warn: (msg, extra) => emit('warn', msg, extra),
    error: (msg, extra) => emit('error', msg, extra),
  };
}

// Test-only: expose internals so unit tests can drive the mode and
// inspect what would have been forwarded. Not part of the public API.
export const __testing = { readMode, normalizeExtra, shouldForward };
