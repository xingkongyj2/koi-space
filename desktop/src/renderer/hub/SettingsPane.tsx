import React, { useState, useEffect, useCallback, useRef } from 'react';
import { ConnectionsPane } from './ConnectionsPane';
import { MemoryIndicatorContent } from './MemoryIndicator';
import type { ActionId, KeyBinding } from './keybindings';
import { fallbackShortcutPlatform, keyboardEventToShortcut } from '../../shared/hotkeys';
import { useThemeMode } from '../design/useThemeMode';
import type { ThemeMode } from '../design/themeMode';
import { useToast } from '@/renderer/components/base/Toast';
import {
  PRESETS as SPINNER_VERB_PRESETS,
  useSpinnerVerbsStore,
  MIN_CYCLE_MS,
  MAX_CYCLE_MS,
  type SpinnerPresetId,
} from './chat/spinnerVerbs';

/**
 * Generic settings primitives. Add a new option type and every section that
 * uses it (Appearance, future Density / Accent / etc.) gets the same UI.
 */
interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

interface SettingsRowProps {
  label: string;
  sublabel?: string;
  children: React.ReactNode;
}

function SettingsRow({ label, sublabel, children }: SettingsRowProps): React.ReactElement {
  return (
    <div className="settings-pane__row">
      <div>
        <div className="settings-pane__label">{label}</div>
        {sublabel && <div className="settings-pane__sublabel">{sublabel}</div>}
      </div>
      {children}
    </div>
  );
}

interface SegmentedControlProps<T extends string> {
  value: T;
  options: ReadonlyArray<SegmentedOption<T>>;
  onChange: (value: T) => void;
  ariaLabel: string;
}

function SegmentedControl<T extends string>({ value, options, onChange, ariaLabel }: SegmentedControlProps<T>): React.ReactElement {
  // Plain toggle-button group with aria-pressed, not role="radio". The radio
  // pattern requires roving tabindex + arrow-key nav; for a 3-option theme
  // picker that's overkill and a partial implementation is worse than none.
  return (
    <div className="settings-pane__segmented" role="group" aria-label={ariaLabel}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          aria-pressed={value === opt.value}
          className={`settings-pane__segment${value === opt.value ? ' settings-pane__segment--active' : ''}`}
          title={opt.hint}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

const APPEARANCE_OPTIONS: ReadonlyArray<SegmentedOption<ThemeMode>> = [
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
  { value: 'system', label: '跟随系统', hint: '使用操作系统的外观设置' },
];

function AppearanceSection(): React.ReactElement {
  const { mode, setMode, resolved } = useThemeMode();
  return (
    <div className="settings-card">
      <SettingsRow
        label="主题"
        sublabel={
          mode === 'system'
            ? `跟随系统（${resolved === 'dark' ? '深色' : '浅色'}）。`
            : '设置所有窗口的外观主题。'
        }
      >
        <SegmentedControl
          value={mode}
          options={APPEARANCE_OPTIONS}
          onChange={setMode}
          ariaLabel="主题"
        />
      </SettingsRow>
    </div>
  );
}

function SpinnerVerbsSection(): React.ReactElement {
  const presetId = useSpinnerVerbsStore((s) => s.presetId);
  const customVerbs = useSpinnerVerbsStore((s) => s.customVerbs);
  const cycleMs = useSpinnerVerbsStore((s) => s.cycleMs);
  const setPreset = useSpinnerVerbsStore((s) => s.setPreset);
  const setCustomVerbs = useSpinnerVerbsStore((s) => s.setCustomVerbs);
  const setCycleMs = useSpinnerVerbsStore((s) => s.setCycleMs);

  const [draft, setDraft] = useState(customVerbs.join('\n'));
  // Keep the local textarea in sync when something else mutates the store
  // (e.g. preset reset), but don't fight the user mid-edit.
  const lastSyncedRef = useRef(customVerbs.join('\n'));
  useEffect(() => {
    const next = customVerbs.join('\n');
    if (next !== lastSyncedRef.current && next !== draft) {
      setDraft(next);
      lastSyncedRef.current = next;
    }
  }, [customVerbs, draft]);

  const presetOptions = [
    ...(Object.entries(SPINNER_VERB_PRESETS) as Array<[Exclude<SpinnerPresetId, 'custom'>, typeof SPINNER_VERB_PRESETS[keyof typeof SPINNER_VERB_PRESETS]]>),
  ];

  const activePreview = presetId === 'custom'
    ? (customVerbs.length > 0 ? customVerbs : ['处理中'])
    : SPINNER_VERB_PRESETS[presetId].verbs;

  const commitDraft = (): void => {
    const next = draft.split('\n').map((v) => v.trim()).filter(Boolean);
    setCustomVerbs(next);
    lastSyncedRef.current = next.join('\n');
  };

  return (
    <div className="settings-card">
      <SettingsRow
        label="运行提示词"
        sublabel="任务运行时，在加载动画旁轮流显示这些提示词。"
      >
        <select
          className="settings-pane__select"
          value={presetId}
          onChange={(e) => setPreset(e.target.value as SpinnerPresetId)}
          aria-label="运行提示词预设"
        >
          {presetOptions.map(([id, preset]) => (
            <option key={id} value={id}>{preset.label}</option>
          ))}
          <option value="custom">自定义</option>
        </select>
      </SettingsRow>

      <SettingsRow
        label="预览"
        sublabel={presetId === 'custom'
          ? `${activePreview.length} 个自定义提示词。`
          : SPINNER_VERB_PRESETS[presetId].description}
      >
        <div className="settings-pane__value" style={{ maxWidth: 320, textAlign: 'right' }}>
          {activePreview.slice(0, 6).join(' / ')}{activePreview.length > 6 ? ' ...' : ''}
        </div>
      </SettingsRow>

      {presetId === 'custom' && (
        <SettingsRow
          label="自定义提示词"
          sublabel={'每行填写一个提示词，忽略空行；未填写时显示“处理中”。'}
        >
          <textarea
            className="settings-pane__textarea"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitDraft}
            placeholder={'准备中\n处理中\n思考中'}
            rows={6}
            spellCheck={false}
            style={{ minWidth: 260, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
          />
        </SettingsRow>
      )}

      <SettingsRow
        label="切换间隔"
        sublabel={`每个提示词显示 ${(cycleMs / 1000).toFixed(1)} 秒。`}
      >
        <input
          type="range"
          min={MIN_CYCLE_MS}
          max={MAX_CYCLE_MS}
          step={100}
          value={cycleMs}
          onChange={(e) => setCycleMs(Number(e.target.value))}
          aria-label="提示词切换间隔"
          style={{ width: 200 }}
        />
      </SettingsRow>
    </div>
  );
}

type ElectronPrivacyAPI = {
  get: () => Promise<{ telemetry: boolean; telemetryUpdatedAt: string | null; version: number }>;
  setTelemetry: (optedIn: boolean) => Promise<{ telemetry: boolean; telemetryUpdatedAt: string | null; version: number }>;
  openSystemNotifications: () => Promise<{ ok: boolean; error?: string }>;
};

type ElectronAppAPI = {
  getUpdateStatus: () => Promise<UpdateStatusEvent>;
  getInfo: () => Promise<{
    version: string;
    latestVersion: string | null;
    isLatestVersion: boolean | null;
    platform: string;
    packaged: boolean;
    updateSupported: boolean;
    canDownloadUpdate: boolean;
    updateFeedUrl: string;
  }>;
  downloadLatest: () => Promise<{
    ok: boolean;
    action: 'started-update-check' | 'unavailable';
    message: string;
  }>;
  installUpdate: () => Promise<{
    ok: boolean;
    action: 'install-started' | 'not-ready';
    message: string;
  }>;
  onUpdateStatus: (cb: (event: UpdateStatusEvent) => void) => () => void;
};

type UpdateStatusEvent = {
  status: 'idle' | 'checking' | 'downloading' | 'ready' | 'error' | 'unavailable';
  version?: string;
  message?: string;
  error?: string;
  progress?: {
    percent: number | null;
    transferred: number | null;
    total: number | null;
    bytesPerSecond: number | null;
  };
};

function AppSection(): React.ReactElement {
  const [info, setInfo] = useState<Awaited<ReturnType<ElectronAppAPI['getInfo']>> | null>(null);
  const [updateStatusEvent, setUpdateStatusEvent] = useState<UpdateStatusEvent>({ status: 'idle' });
  const [checking, setChecking] = useState(false);
  const [installing, setInstalling] = useState(false);
  const api = window.electronAPI?.settings?.app;
  const onLatest = info?.isLatestVersion === true;
  const canDownloadUpdate = info?.canDownloadUpdate === true;
  const updateReady = updateStatusEvent.status === 'ready';
  const updateBusy = updateStatusEvent.status === 'checking' || updateStatusEvent.status === 'downloading';
  const updateActionDisabled = !api || !info || installing || (
    !updateReady && (checking || updateBusy || onLatest || !canDownloadUpdate)
  );
  const downloadProgress = updateStatusEvent.progress?.percent;
  const progressWidth = typeof downloadProgress === 'number'
    ? `${Math.max(2, Math.min(100, downloadProgress))}%`
    : updateStatusEvent.status === 'downloading'
      ? '18%'
      : '0%';
  const updateStatus = updateStatusEvent.status === 'error'
    ? '更新失败，请稍后重试。'
    : updateStatusEvent.status === 'unavailable'
      ? '当前环境暂不支持应用内更新。'
      : updateStatusEvent.status === 'downloading'
        ? `正在下载更新${typeof downloadProgress === 'number' ? `（${Math.round(downloadProgress)}%）` : '…'}`
        : (
    !info
      ? '正在检测最新版本…'
      : updateReady
        ? '更新已下载，可以重启安装。'
        : updateBusy
          ? '正在检查更新…'
          : onLatest
            ? '当前已是最新版本。'
            : info.latestVersion
              ? `最新版本为 ${info.latestVersion}。`
              : canDownloadUpdate
                ? '启动时及每小时自动检查更新。'
                : '应用内更新仅适用于正式发布版本。'
  );
  const buttonLabel = !info || checking
    ? '检查中…'
    : installing
      ? '重启中…'
      : updateReady
        ? '重启并安装'
        : onLatest
          ? '已是最新版本'
          : canDownloadUpdate
            ? '下载更新'
            : '暂不可用';

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api?.getInfo() ?? Promise.resolve(null),
      api?.getUpdateStatus() ?? Promise.resolve<UpdateStatusEvent>({ status: 'idle' }),
    ])
      .then(([nextInfo, nextStatus]) => {
        if (cancelled) return;
        setInfo(nextInfo);
        setUpdateStatusEvent(nextStatus);
      })
      .catch(() => {
        if (cancelled) return;
        setInfo(null);
        setUpdateStatusEvent({ status: 'error', message: '无法读取更新状态。' });
      });

    const unsubscribe = api?.onUpdateStatus((nextStatus) => {
      setUpdateStatusEvent(nextStatus);
      if (nextStatus.status !== 'ready') setInstalling(false);
    });

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [api]);

  const handleDownloadLatest = useCallback(async () => {
    if (!api || checking || installing || onLatest || updateBusy || updateReady || !canDownloadUpdate) return;
    setChecking(true);
    setUpdateStatusEvent({ status: 'checking', message: '正在检查更新…' });
    try {
      const result = await api.downloadLatest();
      setUpdateStatusEvent((current) => (
        current.status === 'checking' ? { status: result.ok ? 'checking' : 'unavailable', message: result.message } : current
      ));
      const next = await api.getInfo();
      setInfo(next);
    } catch {
      setUpdateStatusEvent({ status: 'error', message: '无法检查更新，请稍后重试。' });
    } finally {
      setChecking(false);
    }
  }, [api, canDownloadUpdate, checking, installing, onLatest, updateBusy, updateReady]);

  const handleInstallUpdate = useCallback(async () => {
    if (!api || installing || !updateReady) return;
    setInstalling(true);
    try {
      const result = await api.installUpdate();
      setUpdateStatusEvent((current) => ({
        ...current,
        message: result.message,
      }));
      if (!result.ok) setInstalling(false);
    } catch {
      setUpdateStatusEvent({ status: 'error', message: '无法重启安装更新，请稍后重试。' });
      setInstalling(false);
    }
  }, [api, installing, updateReady]);

  const handleUpdateClick = updateReady ? handleInstallUpdate : handleDownloadLatest;

  return (
    <div className="settings-card">
      <div className="settings-pane__row">
        <div>
          <div className="settings-pane__label">版本</div>
          <div className="settings-pane__sublabel">
            {info ? `Browser Use ${info.version}` : '正在检测版本…'}
          </div>
        </div>
        {info && <span className="settings-pane__value">v{info.version}</span>}
      </div>
      <div className="settings-pane__row">
        <div>
          <div className="settings-pane__label">应用更新</div>
          <div className="settings-pane__sublabel">
            {updateStatus}
          </div>
          {(updateStatusEvent.status === 'downloading' || updateStatusEvent.status === 'ready') && (
            <div className="settings-pane__progress" aria-hidden="true">
              <span
                className="settings-pane__progress-fill"
                style={{ width: updateStatusEvent.status === 'ready' ? '100%' : progressWidth }}
              />
            </div>
          )}
        </div>
        <button
          className="conn-card__btn conn-card__btn--secondary"
          onClick={handleUpdateClick}
          disabled={updateActionDisabled}
        >
          {buttonLabel}
        </button>
      </div>
    </div>
  );
}

function PrivacySection(): React.ReactElement {
  const [telemetry, setTelemetry] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const api = (window as unknown as { electronAPI: { settings: { privacy: ElectronPrivacyAPI } } }).electronAPI.settings.privacy;
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    api.get().then((state) => {
      if (!cancelled) setTelemetry(state.telemetry);
    }).catch(() => { if (!cancelled) setTelemetry(false); });
    return () => { cancelled = true; };
  }, [api]);

  const handleToggle = useCallback(async () => {
    if (telemetry === null || saving) return;
    const next = !telemetry;
    setSaving(true);
    setTelemetry(next); // optimistic
    try {
      const res = await api.setTelemetry(next);
      setTelemetry(res.telemetry);
      toast.show({
        variant: 'success',
        title: res.telemetry ? '已开启匿名使用统计' : '已关闭匿名使用统计',
      });
    } catch {
      setTelemetry(!next); // revert
      toast.show({
        variant: 'error',
        title: '无法保存设置',
        message: '无法保存使用统计设置，请重试。',
      });
    } finally {
      setSaving(false);
    }
  }, [telemetry, saving, api, toast]);

  return (
    <div className="settings-card">
      <div className="settings-pane__row">
        <div>
          <div className="settings-pane__label">允许匿名使用统计，帮助改进应用</div>
          <div className="settings-pane__sublabel">仅收集匿名信息，包括应用版本、操作系统、功能使用情况和崩溃报告。</div>
        </div>
        <button
          className="settings-pane__toggle"
          role="switch"
          aria-checked={telemetry === true}
          data-on={telemetry === true}
          onClick={handleToggle}
          disabled={telemetry === null || saving}
        >
          <span className="settings-pane__toggle-thumb" />
        </button>
      </div>

      <div className="settings-pane__row">
        <div>
          <div className="settings-pane__label">系统通知</div>
          <div className="settings-pane__sublabel">由操作系统管理通知权限。</div>
        </div>
        <button
          className="conn-card__btn conn-card__btn--secondary"
          onClick={() => { void api.openSystemNotifications(); }}
        >
          打开系统设置
        </button>
      </div>
    </div>
  );
}

export type SettingsSectionId =
  | 'settings-connections'
  | 'settings-browser-sync'
  | 'settings-resources'
  | 'settings-shortcuts'
  | 'settings-privacy'
  | 'settings-appearance'
  | 'settings-application';

export interface SettingsOpenIntent {
  requestId: number;
  sectionId?: SettingsSectionId;
}

const SETTINGS_TABS: Array<{ id: SettingsSectionId; label: string }> = [
  { id: 'settings-appearance', label: '外观' },
  { id: 'settings-connections', label: '连接' },
  { id: 'settings-browser-sync', label: '浏览器同步' },
  { id: 'settings-resources', label: '资源' },
  { id: 'settings-shortcuts', label: '快捷键' },
  { id: 'settings-privacy', label: '隐私' },
  { id: 'settings-application', label: '应用' },
];

interface SettingsPaneProps {
  intent?: SettingsOpenIntent | null;
  keybindings: KeyBinding[];
  overrides: Record<string, string[]>;
  onUpdateBinding: (id: ActionId, keys: string[]) => Promise<boolean>;
  onResetBinding: (id: ActionId) => void;
  onResetAll: () => void;
  formatShortcut: (shortcut: string) => string;
}

interface KeybindRowProps {
  kb: KeyBinding;
  isOverridden: boolean;
  onUpdate: (id: ActionId, keys: string[]) => Promise<boolean>;
  onReset: (id: ActionId) => void;
  platform: string;
  formatShortcut: (shortcut: string) => string;
}

function KeybindRow({ kb, isOverridden, onUpdate, onReset, platform, formatShortcut }: KeybindRowProps): React.ReactElement {
  const [recording, setRecording] = useState(false);
  const [firstKey, setFirstKey] = useState<string | null>(null);
  const [recordingError, setRecordingError] = useState<string | null>(null);
  const isGlobalShortcut = kb.id === 'action.createPane';

  const finishRecording = useCallback(async (keys: string[]) => {
    setRecording(false);
    setFirstKey(null);
    (document.activeElement as HTMLElement | null)?.blur?.();
    const ok = await onUpdate(kb.id, keys);
    setRecordingError(ok ? null : '此快捷键不可用，请选择其他组合。');
  }, [kb.id, onUpdate]);

  useEffect(() => {
    if (!recording) return;
    const timer = setTimeout(() => {
      if (firstKey) {
        void finishRecording([firstKey]);
      } else {
        setRecording(false);
        setRecordingError('未检测到快捷键，请重新输入。');
      }
    }, firstKey ? 700 : 8000);

    const handler = async (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();

      if (e.key === 'Escape') {
        setRecording(false);
        setFirstKey(null);
        setRecordingError(null);
        return;
      }

      if (e.key === 'Unidentified') {
        clearTimeout(timer);
        setRecording(false);
        setFirstKey(null);
        setRecordingError('此快捷键不可用，请选择其他组合。');
        return;
      }

      const combo = keyboardEventToShortcut(e, platform);
      if (!combo) return;

      if (isGlobalShortcut && !e.metaKey && !e.ctrlKey && !e.altKey) return;

      if (firstKey) {
        clearTimeout(timer);
        await finishRecording([`${firstKey} ${combo}`]);
        return;
      }

      // If modifier present, commit immediately. Else wait briefly for possible chord.
      if (e.metaKey || e.ctrlKey || e.altKey) {
        clearTimeout(timer);
        await finishRecording([combo]);
        return;
      }

      setRecordingError(null);
      setFirstKey(combo);
    };
    window.addEventListener('keydown', handler, true);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('keydown', handler, true);
    };
  }, [finishRecording, firstKey, isGlobalShortcut, platform, recording]);

  return (
    <div className={`settings-pane__row${isOverridden ? ' settings-pane__row--modified' : ''}`}>
      <div className="settings-pane__label-block">
        <span className="settings-pane__label">{kb.label}</span>
        <span className="settings-pane__sublabel">{kb.category}</span>
      </div>
      <div className="settings-pane__row-right">
        <button
          className={`settings-pane__key-btn${recording ? ' settings-pane__key-btn--recording' : ''}`}
          onClick={() => {
            setRecordingError(null);
            setRecording(true);
            setFirstKey(null);
          }}
        >
          {recording ? (
            <span className="settings-pane__recording">
              {firstKey ? `${formatShortcut(firstKey)} + ...` : '请按下快捷键…'}
            </span>
          ) : (
            kb.keys.map((k, i) => (
              <kbd key={i} className="settings-pane__kbd">{formatShortcut(k)}</kbd>
            ))
          )}
        </button>
        <button
          className="settings-pane__reset-btn"
          onClick={() => onReset(kb.id)}
          title="恢复默认"
          style={{ visibility: isOverridden && !recording ? 'visible' : 'hidden' }}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M2.5 4.5h4a3 3 0 010 6h-2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4.5 2.5L2.5 4.5 4.5 6.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      {recordingError && <span className="settings-pane__key-error">{recordingError}</span>}
    </div>
  );
}

export function SettingsPane({ intent, keybindings, overrides, onUpdateBinding, onResetBinding, onResetAll, formatShortcut }: SettingsPaneProps): React.ReactElement {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [activeSection, setActiveSection] = useState<SettingsSectionId>(SETTINGS_TABS[0].id);
  const platform = window.electronAPI?.shell?.platform ?? fallbackShortcutPlatform();
  // Cookie sync is unsupported on Windows (Chromium ABE + DevTools hardening),
  // so the Browser Sync tab + section are hidden on win32.
  const tabs = platform === 'win32'
    ? SETTINGS_TABS.filter((tab) => tab.id !== 'settings-browser-sync')
    : SETTINGS_TABS;

  const selectSection = useCallback((id: SettingsSectionId) => {
    setActiveSection(id);
    if (scrollerRef.current) scrollerRef.current.scrollTop = 0;
  }, []);

  useEffect(() => {
    const sectionId = intent?.sectionId;
    if (sectionId) selectSection(platform === 'win32' && sectionId === 'settings-browser-sync' ? 'settings-connections' : sectionId);
  }, [intent?.requestId, intent?.sectionId, platform, selectSection]);

  const onTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number): void => {
    let next: number;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault();
    selectSection(tabs[next].id);
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-settings-tab="${tabs[next].id}"]`)?.focus();
  };

  return (
    <div className="settings-page">
      <div className="settings-page__content">
        <header className="settings-page__header">
          <h1 className="settings-page__title">设置</h1>
        </header>
        <nav className="settings-page__tabs" role="tablist" aria-label="设置模块">
          {tabs.map((tab, index) => (
            <button
              key={tab.id}
              id={`${tab.id}-tab`}
              type="button"
              role="tab"
              aria-selected={activeSection === tab.id}
              aria-controls="settings-active-panel"
              tabIndex={activeSection === tab.id ? 0 : -1}
              className={`settings-page__tab${activeSection === tab.id ? ' settings-page__tab--active' : ''}`}
              onClick={() => selectSection(tab.id)}
              onKeyDown={(event) => onTabKeyDown(event, index)}
              data-settings-tab={tab.id}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <div className="settings-page__scroller" ref={scrollerRef}>
          <div id="settings-active-panel" role="tabpanel" aria-labelledby={`${activeSection}-tab`} tabIndex={0}>
            {activeSection === 'settings-application' && (
              <section className="settings-page__section">
                <AppSection />
              </section>
            )}
            {activeSection === 'settings-appearance' && (
              <section className="settings-page__section">
                <AppearanceSection />
                <SpinnerVerbsSection />
              </section>
            )}
            {(activeSection === 'settings-connections' || activeSection === 'settings-browser-sync') && (
              <ConnectionsPane embedded section={activeSection === 'settings-connections' ? 'connections' : 'browser-sync'} />
            )}
            {activeSection === 'settings-resources' && (
              <section className="settings-page__section">
                <div className="settings-card settings-card--resources"><MemoryIndicatorContent /></div>
              </section>
            )}
            {activeSection === 'settings-shortcuts' && (
              <section className="settings-page__section">
                {Object.keys(overrides).length > 0 && (
                  <div className="settings-section-header" style={{ justifyContent: 'flex-end' }}>
                    <button className="settings-pane__reset-all" onClick={onResetAll}>全部恢复默认</button>
                  </div>
                )}
                <div className="settings-card settings-card--shortcuts">
                  {keybindings.map((kb) => (
                    <KeybindRow key={kb.id} kb={kb} isOverridden={kb.id in overrides} onUpdate={onUpdateBinding} onReset={onResetBinding} platform={platform} formatShortcut={formatShortcut} />
                  ))}
                </div>
              </section>
            )}
            {activeSection === 'settings-privacy' && (
              <section className="settings-page__section">
                <PrivacySection />
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
