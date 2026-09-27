// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, it, expect, vi } from 'vitest';
import { useBrowserCard } from '../../../src/renderer/hub/useBrowserCard';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('browser card interaction', () => {
  it('notifies native layout on drag, restores size after expansion, and recenters', () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1200);
    const height = vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(800);
    const bounds = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 1200, height: 800 } as DOMRect);
    const capture = vi.fn();
    let card!: ReturnType<typeof useBrowserCard>;
    function Fixture() { card = useBrowserCard(); return <div ref={card.stageRef}><div style={card.style} /></div>; }
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const changed = vi.fn();
    window.addEventListener('pane:geometry-change', changed);
    try {
      act(() => root.render(<Fixture />));
      expect(card.style.left).toBe(260);
      expect(card.style.top).toBe(150);
      changed.mockClear();
      act(() => card.start({ button: 0, target: container, currentTarget: { setPointerCapture: capture }, preventDefault() {}, pointerId: 1, clientX: 200, clientY: 100 } as unknown as React.PointerEvent<HTMLElement>));
      act(() => card.move({ pointerId: 1, clientX: 240, clientY: 125 } as React.PointerEvent<HTMLElement>));
      act(() => card.end());
      expect(card.style.left).toBe(300);
      expect(card.style.top).toBe(175);
      expect(changed).toHaveBeenCalled();
      act(() => card.resizeBy(-100, -80));
      const floating = { ...card.style };
      expect(floating.width).toBe(580);
      expect(floating.height).toBe(420);
      act(() => card.toggleExpanded());
      expect(card.style).toMatchObject({ left: 0, top: 0, width: 1200, height: 800 });
      act(() => card.toggleExpanded());
      expect(card.style).toEqual(floating);
      act(() => card.center());
      expect(card.style).toMatchObject({ left: 260, top: 150, width: 680, height: 500 });
    } finally {
      act(() => root.unmount());
      container.remove();
      window.removeEventListener('pane:geometry-change', changed);
      width.mockRestore(); height.mockRestore(); bounds.mockRestore();
      vi.unstubAllGlobals();
    }
  });
});
