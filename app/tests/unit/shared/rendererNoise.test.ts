import { describe, expect, it } from 'vitest';
import { isIgnorableRendererLog, isIgnorableRendererMessage } from '@/shared/rendererNoise';

describe('rendererNoise', () => {
  it('matches Chromium ResizeObserver delivery warnings', () => {
    expect(isIgnorableRendererMessage('ResizeObserver loop limit exceeded')).toBe(true);
    expect(isIgnorableRendererMessage('ResizeObserver loop completed with undelivered notifications.')).toBe(true);
  });

  it('does not match ordinary renderer failures', () => {
    expect(isIgnorableRendererMessage('Cannot read properties of undefined')).toBe(false);
  });

  it('matches structured renderer.error payloads by extra.message', () => {
    expect(isIgnorableRendererLog('renderer.error', {
      message: 'ResizeObserver loop completed with undelivered notifications.',
    })).toBe(true);
  });
});
