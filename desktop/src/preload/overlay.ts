/**
 * Preload for the takeover overlay view.
 *
 * One shared document is loaded per session view, so the session it paints
 * over arrives as a query param. Resolving it here (rather than letting the
 * renderer pass one) keeps the overlay's stop button bound to its own session.
 */

import { contextBridge, ipcRenderer } from 'electron';

export interface TakeoverModeState {
  mode: 'idle' | 'active';
  subtitle: string;
}

function readSessionId(): string {
  try {
    return new URLSearchParams(window.location.search).get('session') ?? '';
  } catch {
    return '';
  }
}

const sessionId = readSessionId();

contextBridge.exposeInMainWorld('takeoverAPI', {
  sessionId,
  onMode: (cb: (state: TakeoverModeState) => void): (() => void) => {
    const handler = (_event: unknown, state: TakeoverModeState): void => {
      if (!state || (state.mode !== 'idle' && state.mode !== 'active')) return;
      cb(state);
    };
    ipcRenderer.on('takeover:mode', handler);
    return () => ipcRenderer.removeListener('takeover:mode', handler);
  },
  stop: (): Promise<unknown> =>
    sessionId ? ipcRenderer.invoke('takeover:stop', sessionId) : Promise.resolve({ ok: false }),
});
