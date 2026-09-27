import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { CookieBrowser, type CookieBrowserApi } from '../shared/CookieBrowser';

type WaStatus = 'disconnected' | 'connecting' | 'qr_ready' | 'connected' | 'error';

interface ConnectionsPaneProps {
  embedded?: boolean;
  section?: 'connections' | 'browser-sync';
  connectionsSectionId?: string;
  browserSyncSectionId?: string;
}

export function ConnectionsPane({
  embedded,
  section,
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
    waStatus === 'connected' ? `已连接：${waIdentity ?? '未知账号'}` :
    waStatus === 'connecting' ? '连接中…' :
    waStatus === 'qr_ready' ? '等待扫码…' :
    waStatus === 'error' ? (waDetail && /[\u4e00-\u9fff]/u.test(waDetail) ? waDetail : '连接失败，请重试') :
    '未连接';

  return (
    <div className={embedded ? 'conn-section' : 'conn-pane'}>
      {!embedded && <span className="conn-pane__title">连接</span>}

      {section !== 'browser-sync' && (
      <section
        id={connectionsSectionId}
        className={embedded ? 'settings-page__section' : 'conn-pane__group'}
      >
      {!embedded && (
        <div className="settings-section-header">
          <h2 className="settings-section-header__title">连接</h2>
        </div>
      )}

      <div className="conn-card">
        <div className="conn-card__header">
          <img
            className="conn-card__icon"
            src="https://static.whatsapp.net/rsrc.php/v3/yP/r/rYZqPCBaG70.png"
            alt=""
          />
          <div className="conn-card__info">
            <div className="conn-card__title-row">
              <span className="conn-card__name">微信Bot</span>
              <span className={`conn-card__dot ${statusDotClass}`} />
            </div>
            <span className="conn-card__subtitle">
              {waStatus === 'connected' && waIdentity
                ? `已连接：+${waIdentity}。向自己发送 @BU 加任务内容即可启动会话，例如“@BU 帮我查询去纽约的航班”。未包含 @BU 的消息不会触发任务。`
                : waStatus === 'disconnected'
                ? '连接 WhatsApp 后，向自己发送 @BU 加任务内容即可启动会话，并在同一聊天中接收任务通知。'
                : statusText}
            </span>
          </div>
          <div className="conn-card__actions">
            {waStatus === 'disconnected' && (
              <button className="conn-card__btn conn-card__btn--primary" onClick={handleConnect}>
                连接
              </button>
            )}
            {(waStatus === 'qr_ready' || waStatus === 'connecting') && (
              <button className="conn-card__btn conn-card__btn--secondary" onClick={handleCancel}>
                取消
              </button>
            )}
            {waStatus === 'connected' && (
              <button className="conn-card__btn conn-card__btn--secondary" onClick={handleDisconnect}>
                断开连接
              </button>
            )}
            {waStatus === 'error' && (
              <button className="conn-card__btn conn-card__btn--primary" onClick={handleConnect}>
                重新连接
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
                alt="WhatsApp 连接二维码"
              />
            ) : (
              <div className="conn-card__qr-loading">正在生成二维码…</div>
            )}
            <p className="conn-card__qr-hint">
              打开手机上的 WhatsApp，进入“已关联设备”并扫描二维码。关联后，向自己发送 @BU 加任务内容，例如“@BU 整理我的收件箱”，即可启动会话。未包含 @BU 的消息不会触发任务。
            </p>
          </div>
        )}
      </div>

      </section>
      )}

      {/*
        Cookie sync is unsupported on Windows: Chromium 127+ uses App-Bound
        Encryption (v20) tied to the original user-data-dir, so a temp-copy
        profile decrypts to nothing, and launching headless against the real
        profile is blocked by the Chromium DevTools hardening that refuses
        --remote-debugging-port for the default user-data-dir. Hide the
        section entirely on win32 until we have a native v20 decryption path.
      */}
      {section !== 'connections' && window.electronAPI?.shell?.platform !== 'win32' && (
      <section
        id={browserSyncSectionId}
        className={embedded ? 'settings-page__section' : 'conn-pane__group'}
      >
      {!embedded && (
        <div className="settings-section-header">
          <h2 className="settings-section-header__title">浏览器同步</h2>
        </div>
      )}

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
                <span className="conn-card__name">浏览器登录状态</span>
                <span className="conn-card__dot conn-card__dot--disconnected" />
              </div>
              <span className="conn-card__subtitle">
                当前环境不支持浏览器登录状态同步。
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
