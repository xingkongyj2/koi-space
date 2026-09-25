import React, { useState, useEffect, useCallback, useRef } from 'react';
import { OnboardingCookieList } from './OnboardingCookieList';
import introImage from './intro.png';
import { BrowserLogoAvatar } from '../shared/BrowserLogoAvatar';
import { userFacingIpcError } from '../shared/ipcErrors';
import {
  acceleratorToDisplayParts,
  defaultGlobalCmdbarAccelerator,
  keyboardEventToShortcut,
  normalizeShortcutPlatform,
  rendererToAccelerator,
} from '../../shared/hotkeys';

interface ChromeProfile {
  id: string;
  directory: string;
  browserKey: string;
  browserName: string;
  name: string;
  email: string;
  avatarIcon: string;
}

interface CookieImportResult {
  profileId: string;
  browserName: string;
  profileDirectory: string;
  total: number;
  imported: number;
  failed: number;
  skipped: number;
  domains: string[];
  failedDomains: string[];
  errorReasons: Record<string, number>;
}

declare global {
  interface Window {
    onboardingAPI: {
      detectChromeProfiles: () => Promise<ChromeProfile[]>;
      importChromeProfileCookies: (profileId: string) => Promise<CookieImportResult>;
      listSessionCookies: () => Promise<Array<{
        name: string;
        domain: string;
        path: string;
        secure: boolean;
        httpOnly: boolean;
        expires: number | null;
        sameSite: string;
      }>>;
      getChromeProfileSyncs: () => Promise<Record<string, {
        last_synced_at: string;
        imported: number;
        total: number;
        domain_count: number;
        new_cookies?: number;
        updated_cookies?: number;
        unchanged_cookies?: number;
        new_domain_count?: number;
        updated_domain_count?: number;
      }>>;
      openExternal: (url: string) => Promise<{ opened: boolean }>;
      requestNotifications: () => Promise<{ supported: boolean }>;
      platform: string;
      getPlatform: () => Promise<string>;
      listenShortcut: () => Promise<{ ok: boolean; accelerator: string }>;
      setShortcut: (accelerator: string) => Promise<{ ok: boolean; accelerator: string }>;
      triggerShortcut: () => Promise<{ ok: boolean }>;
      onShortcutActivated: (cb: () => void) => () => void;
      onTaskSubmitted: (cb: () => void) => () => void;
      onPillShown: (cb: () => void) => () => void;
      onPillHidden: (cb: () => void) => () => void;
      getConsent: () => Promise<{ telemetry: boolean; telemetryUpdatedAt: string | null; version: number }>;
      setTelemetryConsent: (optedIn: boolean) => Promise<{ telemetry: boolean; telemetryUpdatedAt: string | null; version: number }>;
      capture: (name: string, props?: Record<string, string | number | boolean>) => void;
      complete: (opts?: { initialHubView?: 'dashboard' | 'grid' | 'list' }) => Promise<void>;
      getState: () => Promise<{ lastStep: string | null }>;
      setStep: (step: string) => Promise<void>;
      whatsapp: {
        connect: () => Promise<{ status: string }>;
        disconnect: () => Promise<{ status: string }>;
        status: () => Promise<{ status: string; identity: string | null }>;
      };
      onWhatsappQr: (cb: (dataUrl: string) => void) => () => void;
      onChannelStatus: (cb: (channelId: string, status: string, detail?: string) => void) => () => void;
    };
  }
}

type Step = 'intro' | 'profile' | 'notifications' | 'shortcut';

function buildAccelerator(e: KeyboardEvent, platform: string): string | null {
  const shortcut = keyboardEventToShortcut(e, platform);
  if (!shortcut || (!e.metaKey && !e.ctrlKey && !e.altKey)) return null;
  return rendererToAccelerator(shortcut, platform);
}

function acceleratorsMatch(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

function PreferencesStep({
  onContinue,
  onBack,
}: {
  onContinue: () => void;
  onBack: () => void;
}) {
  const [requested, setRequested] = useState(false);
  const [supported, setSupported] = useState(true);
  const [telemetryOptIn, setTelemetryOptIn] = useState(true);
  const [saving, setSaving] = useState(false);

  const handleEnable = useCallback(async () => {
    try {
      const res = await window.onboardingAPI.requestNotifications();
      setSupported(res.supported);
      setRequested(true);
    } catch (err) {
      console.error('[onboarding] requestNotifications failed', err);
      setRequested(true);
    }
  }, []);

  const handleContinue = useCallback(async () => {
    setSaving(true);
    try {
      // Always persist the telemetry choice — including an explicit "no" —
      // so we have a dated consent record and don't re-prompt on next launch.
      await window.onboardingAPI.setTelemetryConsent(telemetryOptIn);
    } catch (err) {
      console.error('[onboarding] setTelemetryConsent failed', err);
    } finally {
      setSaving(false);
      onContinue();
    }
  }, [telemetryOptIn, onContinue]);

  const handlePrivacyLink = useCallback(() => {
    window.onboardingAPI.openExternal?.('https://browser-use.com/privacy');
  }, []);

  return (
    <div className="step-panel">
      <h1 className="step-title">Preferences</h1>
      <p className="step-subtitle">
        A couple of defaults you can change anytime in Settings.
      </p>

      <div className="pref-row">
        <div className="pref-row-body">
          <div className="pref-row-title">Notifications</div>
          <div className="pref-row-desc">
            Get alerts when agents finish tasks, get stuck, or need your input.
          </div>
          {requested && supported && (
            <p className="notif-status">
              Check the system dialog to allow notifications.
            </p>
          )}
          {requested && !supported && (
            <p className="notif-status notif-status-error">
              Notifications aren&rsquo;t supported in this environment.
            </p>
          )}
        </div>
        <button
          className="btn btn-secondary pref-row-action"
          onClick={handleEnable}
          disabled={requested}
        >
          {requested ? 'Requested' : 'Enable'}
        </button>
      </div>

      <label className="pref-row pref-row-toggle">
        <input
          type="checkbox"
          checked={telemetryOptIn}
          onChange={(e) => setTelemetryOptIn(e.target.checked)}
        />
        <div className="pref-row-body">
          <div className="pref-row-title">Allow telemetry to help us make this app better</div>
          <div className="pref-row-desc">
            Anonymous usage only — no prompts, credentials, or file contents.{' '}
            <a
              href="#"
              onClick={(e) => { e.preventDefault(); handlePrivacyLink(); }}
            >
              Learn more
            </a>
          </div>
        </div>
      </label>

      <div className="apikey-actions">
        <button className="btn btn-primary" onClick={handleContinue} disabled={saving}>
          {saving ? 'Saving…' : 'Continue'}
        </button>
      </div>

      <div className="step-subactions">
        <button className="back-btn" onClick={onBack}>
          Back
        </button>
      </div>
    </div>
  );
}

const VALID_STEPS: readonly Step[] = ['intro', 'profile', 'notifications', 'shortcut'];

// Cookie sync is unsupported on Windows: Chromium 127+ uses App-Bound
// Encryption (v20) keyed to the original user-data-dir, so a temp-copy
// profile decrypts to nothing, and the alternative path of launching
// headless against the real profile is blocked by the Chromium DevTools
// hardening that refuses --remote-debugging-port for the default profile.
// We hide the onboarding step + Settings card on Windows until we have a
// native v20 decryption path.
const COOKIE_SYNC_SUPPORTED = typeof window !== 'undefined'
  && window.onboardingAPI?.platform !== 'win32';

export function OnboardingApp() {
  const [step, setStep] = useState<Step>('intro');
  const [hydrated, setHydrated] = useState(false);

  // Restore the user's last step on mount — onboarding can be closed mid-flow
  // (e.g. user accidentally dismisses the window) and reopened later. We
  // resume where they left off instead of starting over from intro. Steps
  // persisted by older builds that no longer exist fall through to intro.
  useEffect(() => {
    let cancelled = false;
    window.onboardingAPI.getState?.().then((state) => {
      if (cancelled) return;
      const candidate = state?.lastStep;
      if (candidate && (VALID_STEPS as readonly string[]).includes(candidate)) {
        // If a previous run persisted lastStep === 'profile' (e.g. on a
        // different platform, or before cookie sync was disabled here),
        // skip past it on win32 so the user doesn't land on a hidden step.
        if (candidate === 'profile' && !COOKIE_SYNC_SUPPORTED) {
          setStep('notifications');
        } else {
          setStep(candidate as Step);
        }
      }
      setHydrated(true);
    }).catch(() => { setHydrated(true); });
    return () => { cancelled = true; };
  }, []);

  // Persist the current step so a window close + reopen lands here, not intro.
  useEffect(() => {
    if (!hydrated) return;
    window.onboardingAPI.setStep?.(step).catch(() => { /* best-effort */ });
  }, [step, hydrated]);

  // Fire once on mount — the denominator for the onboarding funnel. Every
  // subsequent drop-off is measured against this event count.
  useEffect(() => {
    window.onboardingAPI.capture?.('onboarding_started');
  }, []);

  // Capture every step transition so PostHog can build a stepwise funnel.
  useEffect(() => {
    window.onboardingAPI.capture?.('onboarding_step_viewed', { step });
  }, [step]);

  const [profiles, setProfiles] = useState<ChromeProfile[]>([]);
  const [loadingProfiles, setLoadingProfiles] = useState(true);
  const [importing, setImporting] = useState<string | null>(null);
  const [importedProfile, setImportedProfile] = useState<ChromeProfile | null>(null);
  const [importResult, setImportResult] = useState<CookieImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const [accelerator, setAccelerator] = useState<string>(() => defaultGlobalCmdbarAccelerator(window.onboardingAPI.platform));
  const [recording, setRecording] = useState(false);
  const [shortcutError, setShortcutError] = useState<string | null>(null);
  const [shortcutActivated, setShortcutActivated] = useState(false);
  const [pillOpen, setPillOpen] = useState(false);
  const [platform, setPlatform] = useState(() => normalizeShortcutPlatform(window.onboardingAPI.platform));
  const suppressRecordingClickUntilRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    window.onboardingAPI.getPlatform?.().then((detectedPlatform) => {
      if (!cancelled) setPlatform(normalizeShortcutPlatform(detectedPlatform));
    }).catch(() => {
      if (!cancelled) setPlatform(normalizeShortcutPlatform(window.onboardingAPI.platform));
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    window.onboardingAPI.detectChromeProfiles().then((p) => {
      setProfiles(p);
      setLoadingProfiles(false);
    }).catch((err) => {
      console.error('[onboarding] detectProfiles failed', err);
      setLoadingProfiles(false);
    });
  }, []);

  const handleImportProfile = useCallback(async (profileId: string) => {
    setImporting(profileId);
    setImportError(null);
    setImportResult(null);
    setImportedProfile(null);
    try {
      const result = await window.onboardingAPI.importChromeProfileCookies(profileId);
      setImportResult(result);
      setImportedProfile(profiles.find((p) => p.id === profileId || p.directory === profileId) ?? null);
    } catch (err) {
      setImportError(userFacingIpcError(err));
    } finally {
      setImporting(null);
    }
  }, [profiles]);

  const handleSkipProfile = useCallback(() => setStep('notifications'), []);

  const handleFinish = useCallback(async () => {
    window.onboardingAPI.capture?.('onboarding_completed');
    try {
      // Tells the main process to land the freshly-opened hub on the
      // Dashboard view (equivalent to `g d`), rather than whatever
      // hub-view-mode was last persisted. Cross-window localStorage
      // isn't shared, so this has to go via main.
      await window.onboardingAPI.complete({ initialHubView: 'dashboard' });
    } catch (err) {
      console.error('[onboarding] complete failed', err);
    }
  }, []);

  // Shortcut step: register the current shortcut, listen for activation + task submission
  useEffect(() => {
    if (step !== 'shortcut') return;
    window.onboardingAPI.listenShortcut().then((res) => {
      if (res.accelerator) setAccelerator(res.accelerator);
      setShortcutError(res.ok ? null : 'That shortcut is unavailable. Choose another one.');
    });
    const unsubActivated = window.onboardingAPI.onShortcutActivated(() => {
      setRecording(false);
      setShortcutActivated(true);
    });
    const unsubShown = window.onboardingAPI.onPillShown(() => {
      setPillOpen(true);
    });
    const unsubHidden = window.onboardingAPI.onPillHidden(() => {
      setPillOpen(false);
    });
    const unsubSubmitted = window.onboardingAPI.onTaskSubmitted(() => {
      // User kicked off a task during onboarding → they want to land on the
      // grid (agent pane) so they can see the running session, not the empty
      // dashboard. Without a submitted task the hub's default 'dashboard'
      // view stands.
      try { window.localStorage.setItem('hub-view-mode', 'grid'); } catch { /* ignore storage failures */ }
      console.log('[onboarding] task submitted during onboarding → hub→grid');
      void window.onboardingAPI.complete();
    });
    return () => { unsubActivated(); unsubShown(); unsubHidden(); unsubSubmitted(); };
  }, [step]);

  // Key recording
  const shouldIgnoreRecordingClick = useCallback((e: React.MouseEvent<HTMLElement>) => {
    return e.detail === 0 || Date.now() < suppressRecordingClickUntilRef.current;
  }, []);

  const startRecording = useCallback(() => {
    setShortcutError(null);
    setRecording(true);
  }, []);

  useEffect(() => {
    if (!recording) return;
    const timeout = window.setTimeout(() => {
      setRecording(false);
      setShortcutError('No shortcut was detected. Choose another combination.');
    }, 8000);
    const handler = async (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Unidentified') {
        window.clearTimeout(timeout);
        setRecording(false);
        setShortcutError('That shortcut is unavailable. Choose another one.');
        return;
      }
      const accel = buildAccelerator(e, platform);
      if (!accel) return;
      window.clearTimeout(timeout);
      suppressRecordingClickUntilRef.current = Date.now() + 700;
      (document.activeElement as HTMLElement | null)?.blur?.();
      setRecording(false);
      try {
        const res = await window.onboardingAPI.setShortcut(accel);
        setAccelerator(res.accelerator);
        setShortcutError(res.ok ? null : 'That shortcut is unavailable. Choose another one.');
        (document.activeElement as HTMLElement | null)?.blur?.();
      } catch (err) {
        console.error('[onboarding] setShortcut failed', err);
        setShortcutError('Shortcut setup failed. Choose another one.');
      }
    };
    window.addEventListener('keydown', handler, true);
    return () => {
      window.clearTimeout(timeout);
      window.removeEventListener('keydown', handler, true);
    };
  }, [recording, platform]);

  useEffect(() => {
    if (step !== 'shortcut' || recording || pillOpen) return;
    const handler = (e: KeyboardEvent) => {
      const accel = buildAccelerator(e, platform);
      if (!accel || !acceleratorsMatch(accel, accelerator)) return;
      e.preventDefault();
      e.stopPropagation();
      void window.onboardingAPI.triggerShortcut().catch((err) => {
        console.error('[onboarding] triggerShortcut failed', err);
      });
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [accelerator, pillOpen, platform, recording, step]);

  return (
    <div className="onboarding-container">
      <div className="onboarding-drag-region" />

      <div className={`onboarding-content ${step === 'intro' ? 'onboarding-content-wide' : ''}`}>
        <div className="step-indicator">
          {((COOKIE_SYNC_SUPPORTED
            ? ['intro', 'profile', 'notifications', 'shortcut']
            : ['intro', 'notifications', 'shortcut']) as Step[]).map((s, i, all) => {
            const currentIdx = all.indexOf(step);
            const thisIdx = i;
            const cls = thisIdx < currentIdx ? 'done' : thisIdx === currentIdx ? 'active' : '';
            return (
              <React.Fragment key={s}>
                <div className={`step-dot ${cls}`} />
                {i < all.length - 1 && <div className="step-line" />}
              </React.Fragment>
            );
          })}
        </div>

        {step === 'intro' && (
          <div className="step-panel intro-panel">
            <div className="intro-content">
              <div className="intro-text">
                <h1 className="intro-title">Browser Use Desktop</h1>
                <p className="intro-subtitle">
                  Run AI agents that browse the web, complete tasks, and report back — all from your desktop.
                </p>
                <button
                  className="btn btn-primary intro-cta"
                  onClick={() => setStep(COOKIE_SYNC_SUPPORTED ? 'profile' : 'notifications')}
                >
                  Get started
                </button>
              </div>
              <div className="intro-image-wrap">
                <img className="intro-image" src={introImage} alt="Browser Use Desktop" />
              </div>
            </div>
          </div>
        )}

        {step === 'profile' && (
          <div className="step-panel">
            <div className="step-title-row">
              <h1 className="step-title">Import Browser Profile</h1>
            </div>
            <p className="step-subtitle">
              Import your cookies so agents can browse as you, or start fresh.
            </p>

            {loadingProfiles && (
              <div className="profile-loading">Detecting browser profiles...</div>
            )}

            {!loadingProfiles && profiles.length === 0 && (
              <div className="profile-empty">
                <p>No Chromium browser profiles found.</p>
                <button className="btn btn-primary" onClick={handleSkipProfile}>
                  Continue without import
                </button>
              </div>
            )}

            {!loadingProfiles && profiles.length > 0 && (
              <div className="profile-list">
                {(importResult ? [] : profiles).map((p) => {
                  const profileId = p.id ?? p.directory;
                  const label = p.name || p.browserName || p.directory;
                  return (
                    <button
                      key={profileId}
                      className="profile-card"
                      onClick={() => handleImportProfile(profileId)}
                      disabled={importing !== null}
                    >
                      <BrowserLogoAvatar
                        browserKey={p.browserKey}
                        fallbackLabel={p.browserName || label}
                        className="profile-browser-logo"
                      />
                      <div className="profile-info">
                        <div className="profile-name">{label}</div>
                        <div className="profile-email">
                          {p.email ? `${p.browserName} · ${p.email}` : p.browserName}
                        </div>
                        <div className="profile-dir">{p.directory}</div>
                      </div>
                      {importing === profileId && (
                        <div className="profile-spinner" />
                      )}
                    </button>
                  );
                })}

                {!importResult && (
                  <button
                    className="profile-card profile-card-skip"
                    onClick={handleSkipProfile}
                    disabled={importing !== null}
                  >
                    <div className="profile-avatar profile-avatar-skip">+</div>
                    <div className="profile-info">
                      <div className="profile-name">Start fresh</div>
                      <div className="profile-email">No cookie import</div>
                    </div>
                  </button>
                )}
              </div>
            )}

            {importResult && (
              <div className="import-results">
                <span className="import-stat import-stat-success">
                  <svg className="import-stat-icon" width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span>
                    Imported {importResult.imported.toLocaleString()} cookies from {importResult.domains.length} domains
                  </span>
                </span>

                {importedProfile && (
                  <div className="import-summary-card">
                    <BrowserLogoAvatar
                      browserKey={importedProfile.browserKey}
                      fallbackLabel={importedProfile.browserName || importedProfile.name || importedProfile.directory}
                      className="profile-browser-logo profile-browser-logo-summary"
                    />
                    <div className="profile-info">
                      <div className="profile-name">{importedProfile.name}</div>
                      <div className="profile-email">
                        {importedProfile.email
                          ? `${importedProfile.browserName} · ${importedProfile.email}`
                          : importedProfile.browserName}
                      </div>
                      <div className="import-summary-meta">
                        {importResult.imported.toLocaleString()} cookies · {importResult.domains.length} domains
                      </div>
                    </div>
                  </div>
                )}

                <OnboardingCookieList
                  listCookies={() => window.onboardingAPI.listSessionCookies()}
                />

                <div className="apikey-actions">
                  <button
                    className="btn btn-primary"
                    onClick={() => setStep('notifications')}
                  >
                    Continue
                  </button>
                </div>

                <button
                  className="back-btn"
                  onClick={() => {
                    setImportResult(null);
                    setImportedProfile(null);
                    setImportError(null);
                    setStep('intro');
                  }}
                >
                  Back
                </button>
              </div>
            )}

            {importError && (
              <div className="import-result import-result-error">
                {importError}
              </div>
            )}

            {!importResult && (
              <button className="back-btn" onClick={() => setStep('intro')}>
                Back
              </button>
            )}
          </div>
        )}

        {step === 'shortcut' && pillOpen && (
          <div className="step-panel pill-takeover">
            <div className="pill-takeover-dot" />
            <h1 className="pill-takeover-title">Pill is open</h1>
            <p className="pill-takeover-subtitle">
              Type a task and press Enter to finish setup.<br/>
              Press Escape to go back.
            </p>
          </div>
        )}

        {step === 'shortcut' && !pillOpen && (
          <div className="step-panel">
            <h1 className="step-title">Set up your global shortcut</h1>
            <p className="step-subtitle">
              Press this shortcut from <strong>any app on your computer</strong> to open the command pill and send a task to an agent.
            </p>

            <div className="shortcut-demo">
              {recording ? (
                <button
                  type="button"
                  className="shortcut-recording shortcut-clickable"
                  onClick={(e) => {
                    if (shouldIgnoreRecordingClick(e)) return;
                    setRecording(false);
                  }}
                  title="Click to cancel"
                >
                  <div className="shortcut-recording-dot" />
                  <span>Press keys...</span>
                </button>
              ) : (
                <button
                  type="button"
                  className="shortcut-keys shortcut-clickable"
                  onClick={(e) => {
                    if (shouldIgnoreRecordingClick(e)) return;
                    startRecording();
                  }}
                  title="Click to change shortcut"
                >
                  {acceleratorToDisplayParts(accelerator, platform).map((key, i, arr) => (
                    <React.Fragment key={i}>
                      <kbd className="kbd">{key}</kbd>
                      {i < arr.length - 1 && <span className="kbd-plus">+</span>}
                    </React.Fragment>
                  ))}
                </button>
              )}
            </div>

            <p className={`shortcut-hint ${shortcutError ? 'shortcut-hint-error' : ''}`}>
              {shortcutError ?? 'Press the shortcut to try it.'}
            </p>

            <div className="apikey-actions">
              <button
                className="btn btn-secondary"
                onClick={(e) => {
                  if (shouldIgnoreRecordingClick(e)) return;
                  if (recording) {
                    setRecording(false);
                  } else {
                    startRecording();
                  }
                }}
              >
                {recording ? 'Cancel' : 'Change shortcut'}
              </button>
              <button className="btn btn-primary" onClick={handleFinish}>
                Skip
              </button>
            </div>

            <button className="back-btn" onClick={() => setStep('notifications')}>
              Back
            </button>
          </div>
        )}

        {step === 'notifications' && (
          <PreferencesStep
            onContinue={() => setStep('shortcut')}
            onBack={() => setStep(COOKIE_SYNC_SUPPORTED ? 'profile' : 'intro')}
          />
        )}
      </div>
    </div>
  );
}
