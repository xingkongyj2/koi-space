/**
 * Tests for the main-side renderer-log IPC payload validator.
 *
 * Keeps the IPC boundary honest: rejects bad payloads with a labelled
 * reason instead of letting them reach the on-disk JSONL log.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';

// vi.mock is hoisted; spies must come from vi.hoisted so they're
// initialized before the mock factory runs.
const { debugSpy, infoSpy, warnSpy, errorSpy } = vi.hoisted(() => ({
  debugSpy: vi.fn(),
  infoSpy: vi.fn(),
  warnSpy: vi.fn(),
  errorSpy: vi.fn(),
}));

vi.mock('@/main/logger', () => ({
  rendererLogger: {
    debug: debugSpy,
    info: infoSpy,
    warn: warnSpy,
    error: errorSpy,
  },
  mainLogger: { debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

import { handleRendererLog } from '@/main/rendererLogIpc';

beforeEach(() => {
  debugSpy.mockClear(); infoSpy.mockClear(); warnSpy.mockClear(); errorSpy.mockClear();
});

describe('handleRendererLog — happy paths', () => {
  it('forwards an error to rendererLogger.error with namespace + extra', () => {
    const result = handleRendererLog('error', 'EnginePicker', 'engineInstall failed', { id: 'codex', code: 7 });
    expect(result).toEqual({ ok: true });
    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy).toHaveBeenCalledWith('engineInstall failed', { ns: 'EnginePicker', id: 'codex', code: 7 });
  });

  it('routes each level to the matching ChannelLogger method', () => {
    handleRendererLog('debug', 'm', 'msg', undefined);
    handleRendererLog('info', 'm', 'msg', undefined);
    handleRendererLog('warn', 'm', 'msg', undefined);
    handleRendererLog('error', 'm', 'msg', undefined);
    expect(debugSpy).toHaveBeenCalledOnce();
    expect(infoSpy).toHaveBeenCalledOnce();
    expect(warnSpy).toHaveBeenCalledOnce();
    expect(errorSpy).toHaveBeenCalledOnce();
  });

  it('tolerates missing extra fields', () => {
    handleRendererLog('info', 'm', 'msg', undefined);
    expect(infoSpy).toHaveBeenCalledWith('msg', { ns: 'm' });
  });

  it('drops Chromium ResizeObserver delivery warnings', () => {
    const result = handleRendererLog('error', 'hub', 'renderer.error', {
      message: 'ResizeObserver loop completed with undelivered notifications.',
      file: 'http://localhost:5173/src/renderer/hub/hub.html',
      line: 0,
    });

    expect(result).toEqual({ ok: true });
    expect(errorSpy).not.toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
  });
});

describe('handleRendererLog — rejections', () => {
  it('rejects an unknown level', () => {
    const r = handleRendererLog('PANIC', 'm', 'msg', undefined);
    expect(r).toEqual({ ok: false, reason: 'invalid-level' });
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('rejects an empty namespace', () => {
    expect(handleRendererLog('info', '', 'msg', undefined)).toEqual({ ok: false, reason: 'invalid-namespace' });
  });

  it('rejects a too-long namespace (>80 chars)', () => {
    expect(handleRendererLog('info', 'x'.repeat(81), 'msg', undefined)).toEqual({ ok: false, reason: 'invalid-namespace' });
  });

  it('rejects a non-string message', () => {
    expect(handleRendererLog('info', 'm', 42, undefined)).toEqual({ ok: false, reason: 'invalid-message' });
  });
});

describe('handleRendererLog — sanitization', () => {
  it('truncates an oversize message (>2000 chars) with an ellipsis', () => {
    const long = 'a'.repeat(5000);
    handleRendererLog('warn', 'm', long, undefined);
    const args = warnSpy.mock.calls[0];
    expect(args[0].length).toBeLessThanOrEqual(2001);
    expect(args[0].endsWith('…')).toBe(true);
  });

  it('caps extra-fields key count at 32', () => {
    const big: Record<string, number> = {};
    for (let i = 0; i < 100; i++) big[`k${i}`] = i;
    handleRendererLog('info', 'm', 'msg', big);
    const fields = infoSpy.mock.calls[0][1] as Record<string, unknown>;
    // 32 + the prepended ns key
    expect(Object.keys(fields).length).toBeLessThanOrEqual(33);
  });

  it('truncates oversized string field values', () => {
    handleRendererLog('info', 'm', 'msg', { huge: 'b'.repeat(5000) });
    const fields = infoSpy.mock.calls[0][1] as { huge: string };
    expect(fields.huge.endsWith('…')).toBe(true);
  });

  it('coerces non-object extra to { value }', () => {
    handleRendererLog('info', 'm', 'msg', 'just a string' as unknown as Record<string, unknown>);
    expect(infoSpy).toHaveBeenCalledWith('msg', { ns: 'm', value: 'just a string' });
  });
});
