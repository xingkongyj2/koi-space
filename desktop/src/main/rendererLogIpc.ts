/**
 * IPC handler for renderer-originated log lines.
 *
 * The renderer's `makeLogger(ns)` forwards warn/error (and info when
 * verbose mode is set) here via the `renderer:log` channel. This module
 * validates the payload and writes it to the existing `renderer` channel
 * logger so the line ends up in `<userData>/logs/renderer.log` as JSONL.
 *
 * Fire-and-forget: the renderer doesn't wait for a response. Logging
 * must never block or throw back at the caller.
 */

import { ipcMain, type IpcMainEvent } from 'electron';
import { rendererLogger } from './logger';
import { isIgnorableRendererLog } from '../shared/rendererNoise';

const ALLOWED_LEVELS = new Set(['debug', 'info', 'warn', 'error']);
const MAX_NS_LEN = 80;
const MAX_MSG_LEN = 2000;
const MAX_EXTRA_KEYS = 32;

type Level = 'debug' | 'info' | 'warn' | 'error';

function sanitizeExtra(value: unknown): Record<string, unknown> | undefined {
  if (value == null) return undefined;
  if (typeof value !== 'object') return { value: String(value) };
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).slice(0, MAX_EXTRA_KEYS);
  const out: Record<string, unknown> = {};
  for (const k of keys) {
    const v = obj[k];
    if (v == null) { out[k] = v; continue; }
    if (typeof v === 'string') { out[k] = v.length > MAX_MSG_LEN ? v.slice(0, MAX_MSG_LEN) + '…' : v; continue; }
    if (typeof v === 'number' || typeof v === 'boolean') { out[k] = v; continue; }
    // Keep arrays/objects but cap size by stringifying
    try {
      const s = JSON.stringify(v);
      out[k] = s.length > MAX_MSG_LEN ? s.slice(0, MAX_MSG_LEN) + '…' : v;
    } catch {
      out[k] = String(v);
    }
  }
  return out;
}

export function handleRendererLog(
  level: unknown,
  ns: unknown,
  msg: unknown,
  extra: unknown,
): { ok: boolean; reason?: string } {
  if (typeof level !== 'string' || !ALLOWED_LEVELS.has(level)) {
    return { ok: false, reason: 'invalid-level' };
  }
  if (typeof ns !== 'string' || ns.length === 0 || ns.length > MAX_NS_LEN) {
    return { ok: false, reason: 'invalid-namespace' };
  }
  if (typeof msg !== 'string') {
    return { ok: false, reason: 'invalid-message' };
  }
  if (isIgnorableRendererLog(msg, extra)) {
    return { ok: true };
  }
  const safeMsg = msg.length > MAX_MSG_LEN ? msg.slice(0, MAX_MSG_LEN) + '…' : msg;
  const safeExtra = sanitizeExtra(extra);
  const fields: Record<string, unknown> = { ns, ...(safeExtra ?? {}) };

  switch (level as Level) {
    case 'debug': rendererLogger.debug(safeMsg, fields); break;
    case 'info': rendererLogger.info(safeMsg, fields); break;
    case 'warn': rendererLogger.warn(safeMsg, fields); break;
    case 'error': rendererLogger.error(safeMsg, fields); break;
  }
  return { ok: true };
}

/** Register the `renderer:log` IPC handler exactly once. */
export function registerRendererLogIpc(): void {
  ipcMain.on(
    'renderer:log',
    (_event: IpcMainEvent, level: unknown, ns: unknown, msg: unknown, extra: unknown) => {
      try {
        const result = handleRendererLog(level, ns, msg, extra);
        if (!result.ok) {
          rendererLogger.warn('renderer:log rejected', { reason: result.reason });
        }
      } catch (err) {
        // Never crash the IPC loop on a bad payload.
        rendererLogger.warn('renderer:log handler threw', {
          error: err instanceof Error ? err.message : String(err),
        });
      }
    },
  );
}
