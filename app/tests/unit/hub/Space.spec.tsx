// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Space } from '../../../src/renderer/hub/Space';
import { useSessionsStore } from '../../../src/renderer/hub/state/sessionsStore';
import type { AgentSession } from '../../../src/renderer/hub/types';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('Space live previews', () => {
  let root: Root;
  let container: HTMLDivElement;
  let intersect: (visible: boolean) => void;
  let frame: (id: string, image: string) => void;
  const start = vi.fn(async () => ({ ok: true }));
  const stop = vi.fn(async () => {});
  const unsubscribe = vi.fn();
  const session: AgentSession = {
    id: 's1', prompt: '浏览网页', createdAt: 1000, status: 'running',
    hasBrowser: true, lastUrl: 'https://example.com', output: [],
  };
  const render = (data = session) => act(() => {
    root.render(<Space sessions={[data]} onSelect={vi.fn()} />);
  });

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    useSessionsStore.getState().hydrate([session]);
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: (entries: Array<{ isIntersecting: boolean }>) => void) {
        intersect = (visible) => callback([{ isIntersecting: visible }]);
      }
      observe() {}
      disconnect() {}
    });
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: {
        sessions: { previewStart: start, previewStop: stop },
        on: { sessionPreviewFrame: (callback: typeof frame) => { frame = callback; return unsubscribe; } },
      },
    });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useSessionsStore.getState().hydrate([]);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('pauses offscreen cards and resumes them with a new owner when visible', () => {
    render();
    expect(start).not.toHaveBeenCalled();
    act(() => intersect(true));
    expect(start).toHaveBeenCalledTimes(1);
    const owner = start.mock.calls[0];
    act(() => intersect(false));
    expect(stop).toHaveBeenCalledWith(...owner);
    expect(unsubscribe).toHaveBeenCalledTimes(1);
    act(() => intersect(true));
    expect(start).toHaveBeenCalledTimes(2);
    expect(start.mock.calls[1]).not.toEqual(owner);
  });

  it('keeps the same stream across navigation and displays the newest frame', () => {
    render();
    act(() => intersect(true));
    render({ ...session, lastUrl: 'https://example.com/next' });
    expect(start).toHaveBeenCalledTimes(1);
    expect(stop).not.toHaveBeenCalled();
    act(() => { frame('s1', 'first'); frame('s1', 'latest'); });
    act(() => vi.advanceTimersByTime(20));
    expect(container.querySelector('img')?.getAttribute('src')).toBe('data:image/jpeg;base64,latest');
  });

  it('starts when the browser-attached state arrives before session metadata', () => {
    useSessionsStore.getState().hydrate([{ ...session, hasBrowser: false }]);
    render({ ...session, hasBrowser: false });
    act(() => intersect(true));
    expect(start).not.toHaveBeenCalled();
    act(() => useSessionsStore.getState().patchSession('s1', { hasBrowser: true }));
    expect(start).toHaveBeenCalledTimes(1);
  });
});
