// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AskForm } from '@/renderer/hub/chat-v2/AskForm';
import type { AskFormPayload } from '@/renderer/hub/chat-v2/htmlBlocks';
import { _resetSubmissionCacheForTests } from '@/renderer/hub/chat-v2/optionListStore';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function renderAsk(payload: AskFormPayload, sessionId = 'session-1'): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<AskForm payload={payload} complete sessionId={sessionId} />);
  });
  return { container, root };
}

function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (!setter) throw new Error('Missing input value setter');
  setter.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('AskForm', () => {
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

  it('does not enable submit for an empty question list', () => {
    const { container, root } = renderAsk({ questions: [] });

    expect(container.querySelector<HTMLButtonElement>('.chatv2-askform__submit')?.disabled).toBe(true);

    act(() => root.unmount());
  });

  it('restores submitted Other text after remounting the same form', async () => {
    const payload: AskFormPayload = {
      questions: [{
        question: 'Which format::exactly?',
        multiSelect: false,
        allowOther: true,
        options: [{ label: 'PDF::file' }],
      }],
    };
    const first = renderAsk(payload);

    act(() => {
      first.container.querySelectorAll<HTMLInputElement>('input[type="radio"]')[1]?.click();
    });
    const otherInput = first.container.querySelector<HTMLInputElement>('.chatv2-askform__other-input');
    expect(otherInput?.disabled).toBe(false);
    act(() => {
      setInputValue(otherInput!, 'Plain text with :: delimiter');
    });
    await act(async () => {
      first.container.querySelector<HTMLButtonElement>('.chatv2-askform__submit')?.click();
      await Promise.resolve();
    });
    act(() => first.root.unmount());

    const second = renderAsk(payload);
    expect(second.container.textContent).toContain('Other: Plain text with :: delimiter');

    act(() => second.root.unmount());
  });

  it('restores submitted Other text by question when question order changes', async () => {
    const firstPayload: AskFormPayload = {
      questions: [
        {
          question: 'First need',
          multiSelect: false,
          allowOther: true,
          options: [{ label: 'Preset A' }],
        },
        {
          question: 'Second need',
          multiSelect: false,
          allowOther: true,
          options: [{ label: 'Preset B' }],
        },
      ],
    };
    const first = renderAsk(firstPayload);

    act(() => {
      const radios = first.container.querySelectorAll<HTMLInputElement>('input[type="radio"]');
      radios[1]?.click();
      radios[3]?.click();
    });
    const otherInputs = first.container.querySelectorAll<HTMLInputElement>('.chatv2-askform__other-input');
    act(() => {
      setInputValue(otherInputs[0]!, 'alpha');
      setInputValue(otherInputs[1]!, 'beta');
    });
    await act(async () => {
      first.container.querySelector<HTMLButtonElement>('.chatv2-askform__submit')?.click();
      await Promise.resolve();
    });
    act(() => first.root.unmount());

    const second = renderAsk({ questions: [...firstPayload.questions].reverse() });
    const answers = Array.from(second.container.querySelectorAll('.chatv2-askform__answer'))
      .map((node) => node.textContent ?? '');

    expect(answers[0]).toContain('Second need');
    expect(answers[0]).toContain('Other: beta');
    expect(answers[1]).toContain('First need');
    expect(answers[1]).toContain('Other: alpha');

    act(() => second.root.unmount());
  });
});
