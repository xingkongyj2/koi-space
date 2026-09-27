// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HtmlBlock } from '@/renderer/hub/chat-v2/HtmlBlock';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let resizeCallback: ResizeObserverCallback | null = null;

class TestResizeObserver implements ResizeObserver {
  readonly observe = vi.fn();
  readonly unobserve = vi.fn();
  readonly disconnect = vi.fn();

  constructor(callback: ResizeObserverCallback) {
    resizeCallback = callback;
  }
}

function renderHtmlBlock(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<HtmlBlock content="<div style='height: 900px'>Tall</div>" complete />);
  });
  return { container, root };
}

function attachTallDocument(iframe: HTMLIFrameElement): void {
  Object.defineProperty(iframe, 'contentDocument', {
    configurable: true,
    value: {
      documentElement: { scrollHeight: 900 },
      body: { scrollHeight: 900 },
    },
  });
}

describe('HtmlBlock', () => {
  beforeEach(() => {
    resizeCallback = null;
    vi.stubGlobal('ResizeObserver', TestResizeObserver);
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('does not auto-collapse again after the user expands tall content', () => {
    const { container, root } = renderHtmlBlock();
    const iframe = container.querySelector<HTMLIFrameElement>('iframe');
    expect(iframe).not.toBeNull();
    attachTallDocument(iframe!);

    act(() => {
      iframe!.dispatchEvent(new Event('load', { bubbles: true }));
    });
    const button = container.querySelector<HTMLButtonElement>('.chatv2-htmlblock__toggle');
    expect(button?.textContent).toBe('Expand');

    act(() => {
      button?.click();
    });
    expect(button?.textContent).toBe('Collapse');

    act(() => {
      resizeCallback?.([], {} as ResizeObserver);
    });
    expect(button?.textContent).toBe('Collapse');

    act(() => root.unmount());
  });
});
