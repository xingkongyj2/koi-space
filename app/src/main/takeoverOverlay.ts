/**
 * Takeover overlay — a sibling WebContentsView that paints a pulsing edge
 * glow on top of the session's browser view while automation is running.
 *
 * Its document renders the shared AI control effect
 * (src/renderer/components/ai-control-effect): a particle wave, the edge glow
 * and a bottom banner whose stop button cancels the session, handing the
 * browser back to the user.
 *
 * Why WebContentsView (not BrowserWindow):
 *   - Bounds live in hub-local coordinates, so the overlay resizes in the
 *     same frame as the browser view when the user drags the split or the
 *     hub window itself. A BrowserWindow requires screen-coord translation
 *     and lags by one IPC round-trip per ResizeObserver tick.
 *   - Z-order is literally the order of children in contentView — add the
 *     overlay after the browser view and it stacks on top. No alwaysOnTop
 *     level juggling.
 *   - Input blocking is implicit: mouse events route to the topmost view
 *     at the cursor, so the browser view beneath never sees clicks/scrolls.
 */
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { WebContentsView, type BrowserWindow } from 'electron';
import { mainLogger } from './logger';

export type OverlayMode = 'idle' | 'active';

interface OverlayEntry {
  sessionId: string;
  view: WebContentsView;
  attached: boolean;
  mode: OverlayMode;
  /** Context line under the banner title — the session's task text. */
  subtitle: string;
  // Whether the overlay renderer has finished loading and is ready to
  // receive takeover:mode IPC. Until then we cache the state on the entry and
  // replay on did-finish-load to avoid races between show() and load.
  loaded: boolean;
}

const entries: Map<string, OverlayEntry> = new Map();

declare const OVERLAY_VITE_DEV_SERVER_URL: string | undefined;

/**
 * The overlay is a real Vite renderer entry (src/renderer/overlay) so the AI
 * control effect has exactly one implementation, shared with the hub's React
 * tree. One built document serves every session; the id rides along as a query
 * param and the preload binds it, so a page can only stop its own session.
 */
const overlayDevUrl =
  typeof OVERLAY_VITE_DEV_SERVER_URL !== 'undefined' && OVERLAY_VITE_DEV_SERVER_URL
    ? `${OVERLAY_VITE_DEV_SERVER_URL}/src/renderer/overlay/overlay.html`
    : null;
const overlayHtmlPath = overlayDevUrl
  ? null
  : path.join(__dirname, '../renderer/overlay/src/renderer/overlay/overlay.html');

function overlayUrl(sessionId: string): string | null {
  const query = `?session=${encodeURIComponent(sessionId)}`;
  if (overlayDevUrl) return `${overlayDevUrl}${query}`;
  if (overlayHtmlPath) return `${pathToFileURL(overlayHtmlPath).toString()}${query}`;
  return null;
}

function createOverlayEntry(sessionId: string, mode: OverlayMode, subtitle: string): OverlayEntry {
  const view = new WebContentsView({
    webPreferences: {
      preload: path.join(__dirname, 'overlay.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  // Transparent background so the particle wave + edge glow sit over the
  // browser view beneath. WebContentsView has no `transparent` webPreference
  // (that's a BrowserWindow option) — use setBackgroundColor with zero alpha.
  view.setBackgroundColor('#00000000');
  const entry: OverlayEntry = { sessionId, view, attached: false, mode, subtitle, loaded: false };
  // Not `once`: a Vite dep-optimisation reload (or a renderer crash) reloads
  // this document after the entry was cached, and the new page starts in idle.
  // Re-pushing on every load is a no-op when nothing changed.
  view.webContents.on('did-finish-load', () => {
    entry.loaded = true;
    pushState(entry);
  });
  const url = overlayUrl(sessionId);
  if (!url) {
    mainLogger.error('takeoverOverlay.load.noUrl', { sessionId });
    return entry;
  }
  view.webContents.loadURL(url).catch((err) => {
    mainLogger.warn('takeoverOverlay.load.error', { sessionId, error: (err as Error).message });
  });
  return entry;
}

function pushState(entry: OverlayEntry): void {
  if (!entry.loaded || entry.view.webContents.isDestroyed()) return;
  try {
    entry.view.webContents.send('takeover:mode', { mode: entry.mode, subtitle: entry.subtitle });
  } catch { /* ignore */ }
}

function pushStateIfChanged(entry: OverlayEntry, mode: OverlayMode, subtitle: string): void {
  const unchanged = entry.mode === mode && entry.subtitle === subtitle;
  entry.mode = mode;
  entry.subtitle = subtitle;
  if (!unchanged) pushState(entry);
}

export function show(
  sessionId: string,
  window: BrowserWindow,
  bounds: { x: number; y: number; width: number; height: number },
  mode: OverlayMode = 'idle',
  subtitle = '',
): void {
  if (!sessionId || !window || window.isDestroyed()) return;
  let entry = entries.get(sessionId);
  if (!entry) {
    entry = createOverlayEntry(sessionId, mode, subtitle);
    entries.set(sessionId, entry);
  } else {
    pushStateIfChanged(entry, mode, subtitle);
  }
  entry.view.setBounds(bounds);
  if (!entry.attached) {
    window.contentView.addChildView(entry.view);
    entry.attached = true;
    mainLogger.info('takeoverOverlay.show', { sessionId, bounds, mode });
  }
}

/** Re-add the overlay above the browser view. Called after BrowserPool attaches
 *  or re-adds its view, since addChildView raises the added view to the top. */
export function reraise(sessionId: string, window: BrowserWindow): void {
  const entry = entries.get(sessionId);
  if (!entry || !entry.attached || !window || window.isDestroyed()) return;
  const children = window.contentView.children;
  if (children.includes(entry.view)) {
    window.contentView.removeChildView(entry.view);
  }
  window.contentView.addChildView(entry.view);
}

export function updateBounds(
  sessionId: string,
  bounds: { x: number; y: number; width: number; height: number },
): void {
  const entry = entries.get(sessionId);
  if (!entry) return;
  entry.view.setBounds(bounds);
}

export function hide(sessionId: string, window: BrowserWindow | null): void {
  const entry = entries.get(sessionId);
  if (!entry) return;
  if (entry.attached && window && !window.isDestroyed()) {
    try { window.contentView.removeChildView(entry.view); } catch { /* ignore */ }
  }
  entry.attached = false;
  entries.delete(sessionId);
  reclaimView(entry);
  mainLogger.info('takeoverOverlay.hide', { sessionId });
}

/**
 * `removeChildView` only detaches — the renderer process stays alive until the
 * webContents is closed. `entries` is keyed per session, so an unreclaimed
 * overlay is a permanent extra Chromium tab for the life of the app. show()
 * builds a fresh entry on demand, so tearing down here costs nothing.
 */
function reclaimView(entry: OverlayEntry): void {
  const wc = entry.view.webContents as unknown as {
    isDestroyed: () => boolean;
    close: (opts?: { waitForBeforeUnload?: boolean }) => void;
    destroy?: () => void;
  };
  try {
    if (!wc.isDestroyed()) wc.close();
  } catch (err) {
    mainLogger.warn('takeoverOverlay.reclaim.closeFailed', {
      sessionId: entry.sessionId,
      error: (err as Error).message,
    });
  }
  setImmediate(() => {
    try {
      if (!wc.isDestroyed()) wc.destroy?.();
    } catch (err) {
      mainLogger.warn('takeoverOverlay.reclaim.destroyFailed', {
        sessionId: entry.sessionId,
        error: (err as Error).message,
      });
    }
  });
}

export function hasOverlay(sessionId: string): boolean {
  return entries.has(sessionId);
}

export function destroyAll(window: BrowserWindow | null): void {
  for (const id of Array.from(entries.keys())) {
    hide(id, window);
  }
}
