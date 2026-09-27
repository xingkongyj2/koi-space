// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Attachment } from '@/renderer/hub/chat-v2/Attachments';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function renderAttachment(props: React.ComponentProps<typeof Attachment>): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<Attachment {...props}><span>file.pdf</span></Attachment>);
  });
  return { container, root };
}

describe('Attachment', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('does not nest the remove button inside another button', () => {
    const { container, root } = renderAttachment({
      onClick: vi.fn(),
      onRemove: vi.fn(),
    });

    expect(container.querySelector('button button')).toBeNull();
    expect(container.querySelector('.chatv2-attachment')?.tagName).toBe('DIV');

    act(() => root.unmount());
  });

  it('keeps remove clicks from activating the attachment click handler', () => {
    const onClick = vi.fn();
    const onRemove = vi.fn();
    const { container, root } = renderAttachment({ onClick, onRemove });

    act(() => {
      container.querySelector<HTMLButtonElement>('.chatv2-attachment__remove')?.click();
    });

    expect(onRemove).toHaveBeenCalledTimes(1);
    expect(onClick).not.toHaveBeenCalled();

    act(() => {
      container.querySelector<HTMLElement>('.chatv2-attachment')?.click();
    });
    expect(onClick).toHaveBeenCalledTimes(1);

    act(() => root.unmount());
  });
});
