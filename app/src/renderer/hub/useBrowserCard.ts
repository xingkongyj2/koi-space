import { useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';
import { centeredBrowserCard, fitBrowserCard } from './browserCardGeometry';
import type { CardArea, CardRect } from './browserCardGeometry';

export function useBrowserCard() {
  const stageRef = useRef<HTMLDivElement>(null);
  const [area, setArea] = useState<CardArea>({ width: 0, height: 0 });
  const [saved, setSaved] = useState<CardRect | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [interacting, setInteracting] = useState(false);
  const gesture = useRef<{ id: number; x: number; y: number; rect: CardRect; resize: boolean; scaleX: number; scaleY: number } | null>(null);
  const floating = saved ? fitBrowserCard(saved, area) : centeredBrowserCard(area);
  const rect = expanded ? { x: 0, y: 0, ...area } : floating;

  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const update = () => setArea({ width: stage.clientWidth, height: stage.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  // ResizeObserver cannot detect a position-only drag. Notify the existing
  // native view synchronizer after React has committed the new card geometry.
  useLayoutEffect(() => {
    window.dispatchEvent(new Event('pane:geometry-change'));
  }, [rect.x, rect.y, rect.width, rect.height]);

  const start = (event: ReactPointerEvent<HTMLElement>, resize = false) => {
    if (expanded || event.button !== 0 || (!resize && (event.target as HTMLElement).closest('button, input, a'))) return;
    const stage = stageRef.current;
    if (!stage) return;
    const bounds = stage.getBoundingClientRect();
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, rect: floating, resize,
      scaleX: stage.clientWidth / bounds.width, scaleY: stage.clientHeight / bounds.height };
    setInteracting(true);
  };
  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current;
    if (!g || g.id !== event.pointerId) return;
    const dx = (event.clientX - g.x) * g.scaleX;
    const dy = (event.clientY - g.y) * g.scaleY;
    const next = g.resize
      ? { ...g.rect, width: Math.min(g.rect.width + dx, area.width - g.rect.x - 24), height: Math.min(g.rect.height + dy, area.height - g.rect.y - 24) }
      : { ...g.rect, x: g.rect.x + dx, y: g.rect.y + dy };
    setSaved(fitBrowserCard(next, area));
  };
  const end = () => { gesture.current = null; setInteracting(false); };
  const toggleExpanded = () => { end(); setExpanded(value => !value); };
  const center = () => { end(); setSaved(null); setExpanded(false); };
  const resizeBy = (dx: number, dy: number) => {
    if (expanded) return;
    setSaved(fitBrowserCard({ ...floating,
      width: Math.min(floating.width + dx, area.width - floating.x - 24),
      height: Math.min(floating.height + dy, area.height - floating.y - 24),
    }, area));
  };
  const style: CSSProperties = { left: rect.x, top: rect.y, width: rect.width, height: rect.height,
    visibility: area.width && area.height ? 'visible' : 'hidden' };
  return { stageRef, style, expanded, interacting, start, move, end, toggleExpanded, center, resizeBy };
}
