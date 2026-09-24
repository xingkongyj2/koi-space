// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OptionList } from '@/renderer/hub/chat-v2/OptionList';
import type { OptionListPayload } from '@/renderer/hub/chat-v2/htmlBlocks';
import { _resetSubmissionCacheForTests } from '@/renderer/hub/chat-v2/optionListStore';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Minimal valid option with required url + site fields
const mkOpt = (id: string, title: string) => ({
  id,
  image: `https://cdn/${id}.jpg`,
  title,
  url: `https://amazon.com/dp/${id}`,
  site: 'Amazon',
});

function renderOptions(payload: OptionListPayload, sessionId = 'session-1'): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<OptionList payload={payload} complete sessionId={sessionId} />);
  });
  return { container, root };
}

function keydown(target: Element, key: string): void {
  target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

describe('OptionList', () => {
  beforeEach(() => {
    _resetSubmissionCacheForTests();
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: {
        sessions: {
          resume: vi.fn(async () => ({ resumed: true })),
        },
      },
    });
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('shows the Other text link when allowOther=true, not a grid card', () => {
    const payload: OptionListPayload = {
      sections: [
        {
          multiSelect: false,
          min: 1,
          max: 1,
          allowOther: true,
          options: [mkOpt('a', 'A')],
        },
      ],
    };
    const { container, root } = renderOptions(payload);

    // Other should be rendered as a link, not as a grid card
    expect(container.querySelector('.chatv2-optlist__card--other')).toBeNull();
    expect(container.querySelector('.chatv2-optlist__other-link')).not.toBeNull();

    act(() => root.unmount());
  });

  it('does not show Other link when allowOther=false (default)', () => {
    const payload: OptionListPayload = {
      sections: [
        {
          multiSelect: false,
          min: 1,
          max: 1,
          allowOther: false,
          options: [mkOpt('b', 'B')],
        },
      ],
    };
    const { container, root } = renderOptions(payload);

    expect(container.querySelector('.chatv2-optlist__other-link')).toBeNull();
    expect(container.querySelector('.chatv2-optlist__card--other')).toBeNull();

    act(() => root.unmount());
  });

  it('submits the option selected by the same Enter keypress', async () => {
    const payload: OptionListPayload = {
      sections: [{
        multiSelect: false,
        min: 1,
        max: 1,
        allowOther: false,
        options: [
          mkOpt('a', 'A'),
          mkOpt('b', 'B'),
        ],
      }],
    };
    const resume = vi.fn<(
      sessionId: string,
      message: string,
    ) => Promise<{ resumed: boolean }>>(async () => ({ resumed: true }));
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: { sessions: { resume } },
    });
    const { container, root } = renderOptions(payload);
    const grid = container.querySelector<HTMLElement>('.chatv2-optlist__grid');
    expect(grid).not.toBeNull();

    act(() => {
      container.querySelectorAll<HTMLButtonElement>('.chatv2-optlist__card')[0]?.click();
    });
    act(() => {
      keydown(grid!, 'ArrowRight');
    });
    await act(async () => {
      keydown(grid!, 'Enter');
      await Promise.resolve();
    });

    expect(resume).toHaveBeenCalledTimes(1);
    expect(resume.mock.calls[0][1]).toContain('id: b');
    expect(resume.mock.calls[0][1]).not.toContain('id: a');

    act(() => root.unmount());
  });

  it('clicking Choose button on a single-select card immediately submits', async () => {
    const payload: OptionListPayload = {
      sections: [{
        multiSelect: false,
        min: 1,
        max: 1,
        allowOther: false,
        options: [mkOpt('x', 'X'), mkOpt('y', 'Y')],
      }],
    };
    const resume = vi.fn(async () => ({ resumed: true }));
    Object.defineProperty(window, 'electronAPI', {
      configurable: true,
      value: { sessions: { resume } },
    });
    const { container, root } = renderOptions(payload);

    // Click the Choose button on the first card
    const chooseBtn = container.querySelector<HTMLButtonElement>('.chatv2-optlist__choose');
    expect(chooseBtn).not.toBeNull();
    await act(async () => {
      chooseBtn!.click();
      await Promise.resolve();
    });

    expect(resume).toHaveBeenCalledTimes(1);
    const call = resume.mock.calls[0] as unknown as [string, string];
    expect(call[1]).toContain('id: x');

    act(() => root.unmount());
  });
});
