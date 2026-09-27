import { ipcMain, BrowserWindow, Notification, shell } from 'electron';
import { mainLogger } from '../logger';
import { AccountStore } from './AccountStore';
import { assertString } from '../ipc-validators';
import { createPillWindow, togglePill, onPillVisibilityChange } from '../pill';
import { normalizeAccelerator } from '../../shared/hotkeys';
import { getGlobalCmdbarAccelerator, registerHotkeys, setGlobalCmdbarAccelerator } from '../hotkeys';

export interface OnboardingHandlerDeps {
  accountStore: AccountStore;
  /** Lazy getter so handlers survive an onboarding window being closed and
   *  reopened (e.g. user closes mid-flow, then reopens via app activation). */
  getOnboardingWindow: () => BrowserWindow | null;
  openShellWindow: () => BrowserWindow;
}

export function registerOnboardingHandlers(deps: OnboardingHandlerDeps): void {
  const { accountStore, getOnboardingWindow, openShellWindow } = deps;

  // Local helper: returns the live window or null if it's been closed/destroyed.
  const liveOnboardingWindow = (): BrowserWindow | null => {
    const w = getOnboardingWindow();
    return w && !w.isDestroyed() ? w : null;
  };

  mainLogger.info('onboardingHandlers.register');

  ipcMain.handle('onboarding:get-state', () => {
    const lastStep = accountStore.getLastOnboardingStep();
    mainLogger.info('onboardingHandlers.getState', { lastStep });
    return { lastStep };
  });

  ipcMain.handle('onboarding:set-step', (_event, step: string) => {
    const validatedStep = assertString(step, 'step', 64);
    accountStore.setLastOnboardingStep(validatedStep);
    mainLogger.debug('onboardingHandlers.setStep', { step: validatedStep });
  });

  ipcMain.handle('onboarding:open-external', async (_event, url: string) => {
    const validated = assertString(url, 'url', 500);
    if (!/^https?:\/\//.test(validated)) throw new Error('onboarding:open-external only accepts http(s) URLs');
    await shell.openExternal(validated);
    return { opened: true };
  });

  let pillCreated = false;

  const fireOnboardingShortcut = (accelerator: string): void => {
    mainLogger.info('onboardingHandlers.shortcutFired', { accelerator });
    togglePill();
    const w = liveOnboardingWindow();
    if (w) w.webContents.send('shortcut-activated');
  };

  const ensureOnboardingShortcut = (): boolean => {
    if (!pillCreated) {
      createPillWindow();
      onPillVisibilityChange((visible) => {
        const w = liveOnboardingWindow();
        if (!w) return;
        w.webContents.send(visible ? 'pill-shown' : 'pill-hidden');
      });
      pillCreated = true;
      mainLogger.info('onboardingHandlers.pillCreated');
    }

    return registerHotkeys(() => fireOnboardingShortcut(getGlobalCmdbarAccelerator()));
  };

  ipcMain.handle('onboarding:listen-shortcut', () => {
    mainLogger.info('onboardingHandlers.listenShortcut', { accelerator: getGlobalCmdbarAccelerator() });
    const ok = ensureOnboardingShortcut();
    return { ok, accelerator: getGlobalCmdbarAccelerator() };
  });

  ipcMain.handle('onboarding:set-shortcut', (_event, accelerator: string) => {
    const validated = assertString(accelerator, 'accelerator', 100);
    const normalized = normalizeAccelerator(validated, process.platform);
    mainLogger.info('onboardingHandlers.setShortcut', { accelerator: normalized });
    ensureOnboardingShortcut();
    const result = setGlobalCmdbarAccelerator(normalized);
    mainLogger.info('onboardingHandlers.setShortcut.persisted', { ...result });
    return result;
  });

  ipcMain.handle('onboarding:trigger-shortcut', () => {
    const accelerator = getGlobalCmdbarAccelerator();
    mainLogger.info('onboardingHandlers.triggerShortcut', { accelerator });
    fireOnboardingShortcut(accelerator);
    return { ok: true };
  });

  ipcMain.handle('onboarding:request-notifications', () => {
    mainLogger.info('onboardingHandlers.requestNotifications');
    if (!Notification.isSupported()) {
      mainLogger.warn('onboardingHandlers.requestNotifications.unsupported');
      return { supported: false };
    }
    const notif = new Notification({
      title: 'Browser Use Desktop',
      body: 'Notifications are on — you\u2019ll hear from your agents here.',
      silent: false,
    });
    notif.show();
    mainLogger.info('onboardingHandlers.requestNotifications.shown');
    return { supported: true };
  });

  ipcMain.handle('onboarding:complete', async (_e, opts?: { initialHubView?: 'dashboard' | 'grid' | 'list' }) => {
    mainLogger.info('onboardingHandlers.complete', { opts });

    const existing = accountStore.load();
    accountStore.save({
      created_at: existing?.created_at,
      onboarding_completed_at: new Date().toISOString(),
      // Clear the resume marker — onboarding is done, no step to return to.
      last_onboarding_step: undefined,
    });

    mainLogger.info('onboardingHandlers.complete.accountSaved');

    await new Promise((resolve) => setTimeout(resolve, 400));

    const shell = openShellWindow();
    mainLogger.info('onboardingHandlers.complete.shellOpened', {
      shellWindowId: shell.id,
    });

    // Cross-window localStorage isn't shared between the onboarding window
    // and the hub, so the onboarding renderer can't preset the view itself.
    // Main sends 'hub:force-view-mode' once the shell has loaded; HubApp
    // listens for it and calls setViewMode. Sent after did-finish-load so
    // the hub's effect listener is mounted by then.
    const initialView = opts?.initialHubView;
    if (initialView) {
      const sendForceView = (): void => {
        if (shell.isDestroyed()) return;
        mainLogger.info('onboardingHandlers.complete.forceViewMode', { initialView });
        shell.webContents.send('hub:force-view-mode', initialView);
      };
      if (shell.webContents.isLoading()) {
        shell.webContents.once('did-finish-load', sendForceView);
      } else {
        sendForceView();
      }
    }

    const w = liveOnboardingWindow();
    if (w) {
      w.close();
      mainLogger.info('onboardingHandlers.complete.onboardingWindowClosed');
    }
  });

  mainLogger.info('onboardingHandlers.register.done');
}

export function unregisterOnboardingHandlers(): void {
  ipcMain.removeHandler('onboarding:get-state');
  ipcMain.removeHandler('onboarding:set-step');
  ipcMain.removeHandler('onboarding:open-external');
  ipcMain.removeHandler('onboarding:listen-shortcut');
  ipcMain.removeHandler('onboarding:set-shortcut');
  ipcMain.removeHandler('onboarding:trigger-shortcut');
  ipcMain.removeHandler('onboarding:request-notifications');
  ipcMain.removeHandler('onboarding:complete');
  mainLogger.info('onboardingHandlers.unregistered');
}
