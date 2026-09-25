import { describe, expect, it } from 'vitest';
import { centeredBrowserCard, fitBrowserCard } from '../../../src/renderer/hub/browserCardGeometry';

describe('browser card bounds', () => {
  it.each([{ width: 1200, height: 800 }, { width: 550, height: 400 }, { width: 300, height: 220 }])('centers the initial card within %j', area => {
    const card = centeredBrowserCard(area);
    expect(card.x * 2 + card.width).toBeCloseTo(area.width);
    expect(card.y * 2 + card.height).toBeCloseTo(area.height);
    expect(card.width).toBeLessThan(area.width);
    expect(card.height).toBeLessThan(area.height);
  });
  it('keeps a dragged card fully inside the right workspace', () => {
    expect(fitBrowserCard({ x: -900, y: 9000, width: 600, height: 400 }, { width: 1000, height: 800 }))
      .toEqual({ x: 24, y: 376, width: 600, height: 400 });
  });
  it('keeps the toolbar usable when resizing too small', () => {
    expect(fitBrowserCard({ x: 24, y: 24, width: 20, height: 20 }, { width: 1000, height: 800 }))
      .toEqual({ x: 24, y: 24, width: 480, height: 320 });
  });
  it('fits a saved large card after the app window becomes smaller', () => {
    const card = fitBrowserCard({ x: 400, y: 200, width: 880, height: 620 }, { width: 460, height: 300 });
    expect(card.x).toBeGreaterThanOrEqual(0);
    expect(card.y).toBeGreaterThanOrEqual(0);
    expect(card.x + card.width).toBeLessThanOrEqual(460);
    expect(card.y + card.height).toBeLessThanOrEqual(300);
  });
});
