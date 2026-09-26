// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsPane, type SettingsOpenIntent } from '../../../src/renderer/hub/SettingsPane';
import type { ActionId, KeyBinding } from '../../../src/renderer/hub/keybindings';
import { ToastProvider } from '../../../src/renderer/components/base/Toast';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../../src/renderer/hub/ConnectionsPane', () => ({
  ConnectionsPane: ({ section }: { section?: string }) => <section data-connection-section={section}>{section === 'connections' ? '连接内容' : '同步内容'}</section>,
}));

const createPaneBinding: KeyBinding = {
  id: 'action.createPane',
  label: 'New pane',
  keys: ['Cmd+Shift+Space'],
  category: 'Actions',
};

function installElectronApi(): void {
  Object.defineProperty(window, 'electronAPI', {
    configurable: true,
    value: {
      shell: { platform: 'darwin' },
      settings: {
        app: {
          getInfo: vi.fn(async () => null),
          getUpdateStatus: vi.fn(async () => ({ status: 'idle' })),
          onUpdateStatus: vi.fn(() => undefined),
          downloadLatest: vi.fn(),
          installUpdate: vi.fn(),
        },
        privacy: {
          get: vi.fn(async () => ({ telemetry: false, telemetryUpdatedAt: null, version: 1 })),
          setTelemetry: vi.fn(async (telemetry: boolean) => ({ telemetry, telemetryUpdatedAt: null, version: 1 })),
          openSystemNotifications: vi.fn(async () => ({ ok: true })),
        },
      },
      on: {},
    },
  });
}

function renderSettingsPane(onUpdateBinding: (id: ActionId, keys: string[]) => Promise<boolean>, intent?: SettingsOpenIntent): {
  container: HTMLDivElement;
  root: Root;
} {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <ToastProvider>
        <SettingsPane
          intent={intent}
          keybindings={[createPaneBinding]}
          overrides={{}}
          onUpdateBinding={onUpdateBinding}
          onResetBinding={vi.fn()}
          onResetAll={vi.fn()}
          formatShortcut={(shortcut) => shortcut}
        />
      </ToastProvider>,
    );
  });
  return { container, root };
}

function keyButton(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>('.settings-pane__key-btn');
  if (!button) throw new Error('Missing key binding button');
  return button;
}

function clickTab(container: HTMLElement, id: string): void {
  act(() => container.querySelector<HTMLButtonElement>(`[data-settings-tab="${id}"]`)?.click());
}

describe('SettingsPane tabs and shortcut recorder', () => {
  beforeEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.mode;
    installElectronApi();
  });

  afterEach(() => {
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  it('records a global shortcut with the same multi-key space capture used by onboarding', async () => {
    const onUpdateBinding = vi.fn(async () => true);
    const { container, root } = renderSettingsPane(onUpdateBinding);
    clickTab(container, 'settings-shortcuts');

    act(() => {
      keyButton(container).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    expect(keyButton(container).textContent).toContain('请按下快捷键');

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', {
        key: '\u00A0',
        code: 'Space',
        metaKey: true,
        altKey: true,
        bubbles: true,
        cancelable: true,
      }));
    });

    expect(onUpdateBinding).toHaveBeenCalledWith('action.createPane', ['Cmd+Alt+Space']);
    expect(container.querySelector('.settings-pane__key-error')).toBeNull();

    act(() => root.unmount());
  });

  it('shows browser sync as a separate Chinese tab and displays only the selected module', () => {
    const { container, root } = renderSettingsPane(vi.fn(async () => true));
    const browserSyncTab = container.querySelector<HTMLButtonElement>('[data-settings-tab="settings-browser-sync"]');

    expect(browserSyncTab?.textContent).toBe('浏览器同步');
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('外观');
    expect(container.querySelector('.settings-pane__segmented')).not.toBeNull();
    clickTab(container, 'settings-browser-sync');
    expect(container.querySelector('.layout-picker')).toBeNull();
    expect(container.querySelector('[data-connection-section]')?.getAttribute('data-connection-section')).toBe('browser-sync');
    expect(browserSyncTab?.getAttribute('aria-selected')).toBe('true');
    clickTab(container, 'settings-connections');
    expect(container.querySelector('[data-connection-section]')?.getAttribute('data-connection-section')).toBe('connections');

    act(() => root.unmount());
  });

  it('places application last, removes the layout setting, and keeps theme selection working', () => {
    const { container, root } = renderSettingsPane(vi.fn(async () => true));
    const tabs = Array.from(container.querySelectorAll('[role="tab"]'));
    expect(tabs.at(-1)?.textContent).toBe('应用');
    clickTab(container, 'settings-application');
    expect(container.querySelector('.layout-picker')).toBeNull();
    expect(container.textContent).not.toContain('会话标签布局');
    clickTab(container, 'settings-appearance');
    const lightButton = Array.from(container.querySelectorAll<HTMLButtonElement>('.settings-pane__segment')).find((button) => button.textContent === '浅色');
    if (!lightButton) throw new Error('Missing light appearance option');
    act(() => lightButton.click());
    expect(window.localStorage.getItem('browser-use:theme-mode')).toBe('light');
    expect(document.documentElement.dataset.mode).toBe('light');
    act(() => root.unmount());
  });

  it('opens the requested module directly and supports keyboard tab switching', () => {
    const { container, root } = renderSettingsPane(vi.fn(async () => true), { requestId: 1, sectionId: 'settings-privacy' });
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('隐私');
    expect(container.querySelector('.settings-pane__toggle')).not.toBeNull();
    expect(container.querySelector('.layout-picker')).toBeNull();
    const selected = container.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]')!;
    act(() => selected.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true })));
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe('外观');
    expect(document.activeElement?.textContent).toBe('外观');
    expect(container.querySelector('.settings-pane__toggle')).toBeNull();
    act(() => root.unmount());
  });

  it('shows an unavailable-shortcut error when the global save is rejected', async () => {
    const onUpdateBinding = vi.fn(async () => false);
    const { container, root } = renderSettingsPane(onUpdateBinding);
    clickTab(container, 'settings-shortcuts');

    act(() => {
      keyButton(container).dispatchEvent(new MouseEvent('click', { bubbles: true }));
    });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', {
        key: ' ',
        code: 'Space',
        metaKey: true,
        shiftKey: true,
        bubbles: true,
        cancelable: true,
      }));
    });

    expect(container.querySelector('.settings-pane__key-error')?.textContent).toBe(
      '此快捷键不可用，请选择其他组合。',
    );

    act(() => root.unmount());
  });
});
