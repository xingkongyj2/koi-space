export interface CardRect { x: number; y: number; width: number; height: number }
export interface CardArea { width: number; height: number }

const GUTTER = 24;
export function fitBrowserCard(rect: CardRect, area: CardArea): CardRect {
  const margin = Math.min(GUTTER, area.width / 12, area.height / 12);
  const maxWidth = Math.max(0, area.width - margin * 2);
  const maxHeight = Math.max(0, area.height - margin * 2);
  const width = Math.min(maxWidth, Math.max(Math.min(480, maxWidth), rect.width));
  const height = Math.min(maxHeight, Math.max(Math.min(320, maxHeight), rect.height));
  return {
    x: Math.min(Math.max(margin, rect.x), area.width - margin - width),
    y: Math.min(Math.max(margin, rect.y), area.height - margin - height),
    width,
    height,
  };
}

export function centeredBrowserCard(area: CardArea): CardRect {
  // Keep the browser visibly detached from the workspace so it reads like
  // Black Bear's generator node. The explicit caps keep the card compact on
  // wide desktop windows while the percentages preserve a usable viewport on
  // smaller layouts.
  const width = Math.min(680, area.width * .72);
  const height = Math.min(500, area.height * .72);
  const fitted = fitBrowserCard({ x: 0, y: 0, width, height }, area);
  return { ...fitted, x: (area.width - fitted.width) / 2, y: (area.height - fitted.height) / 2 };
}
