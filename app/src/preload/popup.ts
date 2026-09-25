import { contextBridge, ipcRenderer } from 'electron';
import type {
  AppPopupAction,
  AppPopupContentSize,
  AppPopupOpenRequest,
} from '../shared/app-popup';

contextBridge.exposeInMainWorld('popupHostAPI', {
  ready: (): void => {
    ipcRenderer.send('app-popup:renderer-ready');
  },
  onRender: (cb: (request: AppPopupOpenRequest) => void): (() => void) => {
    const handler = (_evt: unknown, request: AppPopupOpenRequest): void => cb(request);
    ipcRenderer.on('app-popup:render', handler);
    return () => ipcRenderer.removeListener('app-popup:render', handler);
  },
  contentReady: (popupId: string): void => {
    ipcRenderer.send('app-popup:content-ready', popupId);
  },
  resize: (size: AppPopupContentSize): void => {
    ipcRenderer.send('app-popup:content-size', size);
  },
  action: (action: AppPopupAction): void => {
    ipcRenderer.send('app-popup:action', action);
  },
  close: (popupId: string, reason?: string): void => {
    ipcRenderer.send('app-popup:close-from-popup', { popupId, reason });
  },
});

contextBridge.exposeInMainWorld('electronAPI', {
  sessions: {
    cancel: (id: string): Promise<void> => ipcRenderer.invoke('sessions:cancel', id),
    memory: (): Promise<{
      totalMb: number;
      totalCpuPercent?: number;
      sessions: Array<{ id: string; mb: number; cpuPercent?: number; status: string; processCount?: number }>;
      processes: Array<{
        pid?: number;
        label: string;
        type: string;
        component?: string;
        mb: number;
        cpuPercent?: number;
        sessionId?: string;
        engineId?: string;
        source?: string;
      }>;
      processCount: number;
      errors?: string[];
    }> => ipcRenderer.invoke('sessions:memory'),
  },
  settings: {
    open: (): Promise<void> => ipcRenderer.invoke('settings:open'),
    theme: {
      get: (): Promise<{ mode: 'light' | 'dark' | 'system'; resolved: 'light' | 'dark' }> =>
        ipcRenderer.invoke('theme:get'),
      set: (mode: 'light' | 'dark' | 'system'): Promise<{ mode: 'light' | 'dark' | 'system'; resolved: 'light' | 'dark' }> =>
        ipcRenderer.invoke('theme:set', mode),
      onChange: (cb: (event: { mode: 'light' | 'dark' | 'system'; resolved: 'light' | 'dark' }) => void): (() => void) => {
        const handler = (_evt: unknown, payload: { mode: 'light' | 'dark' | 'system'; resolved: 'light' | 'dark' }) => cb(payload);
        ipcRenderer.on('theme:changed', handler);
        return () => ipcRenderer.removeListener('theme:changed', handler);
      },
    },
  },
});
