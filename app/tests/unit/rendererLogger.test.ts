// @vitest-environment jsdom

/**
 * Tests for the renderer-side structured logger (renderer/shared/logger.ts).
 *
 * Verifies the console output shape, IPC forwarding rules per level/mode,
 * and Error/object normalization. Uses jsdom so window.localStorage and
 * window.electronAPI mocks behave like the real renderer.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { makeLogger, __testing } from '@/renderer/shared/logger';

const logSpy = vi.fn();
const ipcSpy = vi.fn();

beforeEach(() => {
  logSpy.mockClear(); ipcSpy.mockClear();
  vi.stubGlobal('console', {
    ...console,
    log: logSpy,
    debug: logSpy,
    warn: logSpy,
    error: logSpy,
  });
  (window as unknown as { electronAPI: unknown }).electronAPI = {
    log: ipcSpy,
  };
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe('makeLogger — console output', () => {
  it('emits to console with the [namespace] prefix', () => {
    const log = makeLogger('EnginePicker');
    log.info('refreshStatus.failed', { id: 'codex' });
    expect(logSpy).toHaveBeenCalledTimes(1);
    expect(logSpy).toHaveBeenCalledWith('[EnginePicker]', 'refreshStatus.failed', { id: 'codex' });
  });

  it('omits the extra arg when no extra is given', () => {
    makeLogger('m').warn('something');
    expect(logSpy).toHaveBeenCalledWith('[m]', 'something');
  });

  it('mutes console output entirely when localStorage.rendererLog === "silent"', () => {
    window.localStorage.setItem('rendererLog', 'silent');
    makeLogger('m').error('boom', { code: 1 });
    expect(logSpy).not.toHaveBeenCalled();
    // But error still forwards to disk:
    expect(ipcSpy).toHaveBeenCalledTimes(1);
  });
});

describe('makeLogger — IPC forwarding rules', () => {
  it('forwards warn to main', () => {
    makeLogger('m').warn('slow', { ms: 500 });
    expect(ipcSpy).toHaveBeenCalledWith('warn', 'm', 'slow', { ms: 500 });
  });

  it('forwards error to main', () => {
    makeLogger('m').error('failed', { code: 7 });
    expect(ipcSpy).toHaveBeenCalledWith('error', 'm', 'failed', { code: 7 });
  });

  it('does NOT forward debug at any mode', () => {
    makeLogger('m').debug('verbose detail');
    window.localStorage.setItem('rendererLog', 'verbose');
    makeLogger('m').debug('verbose detail 2');
    expect(ipcSpy).not.toHaveBeenCalled();
  });

  it('does NOT forward info by default (normal mode)', () => {
    makeLogger('m').info('routine');
    expect(ipcSpy).not.toHaveBeenCalled();
  });

  it('forwards info ONLY when localStorage.rendererLog === "verbose"', () => {
    window.localStorage.setItem('rendererLog', 'verbose');
    makeLogger('m').info('routine');
    expect(ipcSpy).toHaveBeenCalledWith('info', 'm', 'routine', undefined);
  });

  it('survives a missing electronAPI bridge (test/standalone renderer)', () => {
    delete (window as unknown as { electronAPI?: unknown }).electronAPI;
    expect(() => makeLogger('m').error('still works')).not.toThrow();
  });
});

describe('makeLogger — extra normalization', () => {
  it('expands an Error instance into message/stack/name', () => {
    const err = new Error('boom');
    makeLogger('m').error('caught', err);
    const args = ipcSpy.mock.calls[0];
    expect(args[3]).toMatchObject({ error: 'boom', name: 'Error' });
    expect(typeof (args[3] as { stack: string }).stack).toBe('string');
  });

  it('passes plain objects through as a shallow JSON clone', () => {
    makeLogger('m').warn('x', { a: 1, b: 'two' });
    expect(ipcSpy.mock.calls[0][3]).toEqual({ a: 1, b: 'two' });
  });

  it('coerces non-object extras (string/number) to {value}', () => {
    makeLogger('m').warn('x', 42);
    expect(ipcSpy.mock.calls[0][3]).toEqual({ value: '42' });
  });
});

describe('shouldForward (level/mode matrix)', () => {
  const { shouldForward } = __testing;
  it('warn + error always forward', () => {
    for (const m of ['normal', 'verbose', 'silent'] as const) {
      expect(shouldForward('warn', m)).toBe(true);
      expect(shouldForward('error', m)).toBe(true);
    }
  });
  it('info forwards only in verbose', () => {
    expect(shouldForward('info', 'normal')).toBe(false);
    expect(shouldForward('info', 'verbose')).toBe(true);
    expect(shouldForward('info', 'silent')).toBe(false);
  });
  it('debug never forwards', () => {
    expect(shouldForward('debug', 'normal')).toBe(false);
    expect(shouldForward('debug', 'verbose')).toBe(false);
    expect(shouldForward('debug', 'silent')).toBe(false);
  });
});
