import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { CookieBrowser, type CookieBrowserApi } from '../shared/CookieBrowser';

type WaStatus = 'disconnected' | 'connecting' | 'qr_ready' | 'connected' | 'error';

interface ConnectionsPaneProps {
  embedded?: boolean;
  connectionsSectionId?: string;
  browserSyncSectionId?: string;
}

export function ConnectionsPane({
  embedded,
  connectionsSectionId,
  browserSyncSectionId,
}: ConnectionsPaneProps): React.ReactElement {
  const [waStatus, setWaStatus] = useState<WaStatus>('disconnected');
  const [waIdentity, setWaIdentity] = useState<string | null>(null);
  const [waDetail, setWaDetail] = useState<string | undefined>();
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);

  const cookieBrowserApi = useMemo<CookieBrowserApi | null>(() => {
    const api = window.electronAPI?.chromeImport;
    if (!api) return null;
    return {
      detectProfiles: api.detectProfiles,
      importCookies: api.importCookies,
      listCookies: api.listCookies,
      getSyncs: api.getSyncs,
    };
  }, []);

  useEffect(() => {
    const api = window.electronAPI;
    if (!api) return;

    api.channels?.whatsapp.status().then((res) => {
      setWaStatus(res.status as WaStatus);
      setWaIdentity(res.identity);
    }).catch(() => {});

    const unsubStatus = api.on?.channelStatus?.((channelId, status, detail) => {
      if (channelId !== 'whatsapp') return;
      setWaStatus(status as WaStatus);
      setWaDetail(detail);
      if (status === 'connected' && detail) {
        setWaIdentity(detail);
        setQrDataUrl(null);
      }
      if (status === 'disconnected' || status === 'error') {
        setQrDataUrl(null);
      }
    });

    const unsubQr = api.on?.whatsappQr?.((dataUrl) => {
      setQrDataUrl(dataUrl);
    });

    return () => {
      unsubStatus?.();
      unsubQr?.();
    };
  }, []);

  const handleConnect = useCallback(async () => {
    const api = window.electronAPI;
    if (!api) return;
    setQrDataUrl(null);
    await api.channels.whatsapp.connect();
  }, []);

  const handleDisconnect = useCallback(async () => {
    const api = window.electronAPI;
    if (!api) return;
    await api.channels.whatsapp.clearAuth();
    setWaIdentity(null);
    setQrDataUrl(null);
  }, []);

  const handleCancel = useCallback(async () => {
    const api = window.electronAPI;
    if (!api) return;
    await api.channels.whatsapp.disconnect();
    setQrDataUrl(null);
  }, []);

  const statusDotClass =
    waStatus === 'connected' ? 'conn-card__dot--connected' :
    waStatus === 'connecting' || waStatus === 'qr_ready' ? 'conn-card__dot--connecting' :
    waStatus === 'error' ? 'conn-card__dot--error' :
    'conn-card__dot--disconnected';

  const statusText =
    waStatus === 'connected' ? `Connected as ${waIdentity ?? 'unknown'}` :
    waStatus === 'connecting' ? 'Connecting...' :
    waStatus === 'qr_ready' ? 'Waiting for scan...' :
    waStatus === 'error' ? (waDetail ?? 'Connection error') :
    'Not connected';

  return (
    <div className={embedded ? 'conn-section' : 'conn-pane'}>
      {!embedded && <span className="conn-pane__title">Connections</span>}

      <section
        id={connectionsSectionId}
        className={embedded ? 'settings-page__section' : 'conn-pane__group'}
      >
      <div className="settings-section-header">
        <h2 className="settings-section-header__title">Connections</h2>
      </div>

      <div className="conn-card">
        <div className="conn-card__header">
          <img
            className="conn-card__icon"
            src="https://static.whatsapp.net/rsrc.php/v3/yP/r/rYZqPCBaG70.png"
            alt=""
          />
          <div className="conn-card__info">
            <div className="conn-card__title-row">
              <span className="conn-card__name">WhatsApp</span>
              <span className={`conn-card__dot ${statusDotClass}`} />
            </div>
            <span className="conn-card__subtitle">
              {waStatus === 'connected' && waIdentity
                ? `Connected as +${waIdentity.replace(/(\d{1})(\d{3})(\d{3})(\d{4})/, '$1 ($2) $3-$4')} — text yourself with @BU to start a session (e.g. "@BU find me a flight to NYC"). Messages without @BU are ignored, so the chat still works as a notes app.`
                : waStatus === 'disconnected'
                ? 'Connect WhatsApp so you can text yourself @BU to launch sessions and get agent notifications back in the same chat.'
                : statusText}
            </span>
          </div>
          <div className="conn-card__actions">
            {waStatus === 'disconnected' && (
              <button className="conn-card__btn conn-card__btn--primary" onClick={handleConnect}>
                Connect
              </button>
            )}
            {(waStatus === 'qr_ready' || waStatus === 'connecting') && (
              <button className="conn-card__btn conn-card__btn--secondary" onClick={handleCancel}>
                Cancel
              </button>
            )}
            {waStatus === 'connected' && (
              <button className="conn-card__btn conn-card__btn--secondary" onClick={handleDisconnect}>
                Disconnect
              </button>
            )}
            {waStatus === 'error' && (
              <button className="conn-card__btn conn-card__btn--primary" onClick={handleConnect}>
                Reconnect
              </button>
            )}
          </div>
        </div>

        {(waStatus === 'qr_ready' || qrDataUrl) && (
          <div className="conn-card__qr">
            {qrDataUrl ? (
              <img
                className="conn-card__qr-img"
                src={qrDataUrl}
                alt="WhatsApp QR code"
              />
            ) : (
              <div className="conn-card__qr-loading">Generating QR...</div>
            )}
            <p className="conn-card__qr-hint">
              Open WhatsApp on your phone, go to Linked Devices, and scan this code. After linking, text yourself with @BU followed by a task (e.g. "@BU summarize my Linear inbox") to start a session — plain notes without @BU are ignored.
            </p>
          </div>
        )}
      </div>

      </section>

      {/*
        Cookie sync is unsupported on Windows: Chromium 127+ uses App-Bound
        Encryption (v20) tied to the original user-data-dir, so a temp-copy
        profile decrypts to nothing, and launching headless against the real
        profile is blocked by the Chromium DevTools hardening that refuses
        --remote-debugging-port for the default user-data-dir. Hide the
        section entirely on win32 until we have a native v20 decryption path.
      */}
      {window.electronAPI?.shell?.platform !== 'win32' && (
      <section
        id={browserSyncSectionId}
        className={embedded ? 'settings-page__section' : 'conn-pane__group'}
      >
      <div className="settings-section-header">
        <h2 className="settings-section-header__title">Browser Sync</h2>
      </div>

      {cookieBrowserApi ? (
        <div className="conn-card conn-card--cookies">
          <CookieBrowser api={cookieBrowserApi} />
        </div>
      ) : (
        <div className="conn-card">
          <div className="conn-card__header">
            <div className="conn-card__icon conn-card__icon--letter">C</div>
            <div className="conn-card__info">
              <div className="conn-card__title-row">
                <span className="conn-card__name">Browser cookies</span>
                <span className="conn-card__dot conn-card__dot--disconnected" />
              </div>
              <span className="conn-card__subtitle">
                Cookie sync is unavailable in this environment.
              </span>
            </div>
          </div>
        </div>
      )}
      </section>
      )}
    </div>
  );
}

export default ConnectionsPane;
