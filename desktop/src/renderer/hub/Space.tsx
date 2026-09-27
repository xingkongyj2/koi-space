import React, { useEffect, useRef, useState } from 'react';
import { useSessionsStore } from './state/sessionsStore';
import type { AgentSession, SessionStatus } from './types';

interface SpaceProps {
  sessions: AgentSession[];
  selectedId?: string | null;
  onSelect: (id: string) => void;
}

const STATUS: Record<SessionStatus, { label: string; className: string }> = {
  draft: { label: '草稿', className: 'space-card__status--draft' },
  running: { label: '进行中', className: 'space-card__status--running' },
  stuck: { label: '需要处理', className: 'space-card__status--stuck' },
  paused: { label: '已暂停', className: 'space-card__status--paused' },
  idle: { label: '休眠', className: 'space-card__status--idle' },
  stopped: { label: '已停止', className: 'space-card__status--stopped' },
};

function hostOf(session: AgentSession): string {
  const source = session.lastUrl || session.primarySite || '';
  try {
    return new URL(source.startsWith('http') ? source : `https://${source}`).hostname;
  } catch {
    return '浏览器会话';
  }
}

function SpaceThumbnail({ session }: { session: AgentSession }): React.ReactElement {
  const host = hostOf(session);
  const hasPageAddress = Boolean(session.lastUrl?.trim() || session.primarySite?.trim());
  const [hasFrame, setHasFrame] = useState(false);
  const imageRef = useRef<HTMLImageElement>(null);
  const thumbnailRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  // Browser lifecycle events arrive independently of session metadata updates.
  const hasBrowser = useSessionsStore((state) => state.byId[session.id]?.hasBrowser ?? session.hasBrowser);

  useEffect(() => {
    const element = thumbnailRef.current;
    if (!element) return;
    let intersects = false;
    const updateVisibility = () => setVisible(intersects && !document.hidden);
    const observer = new IntersectionObserver(([entry]) => {
      intersects = entry.isIntersecting;
      updateVisibility();
    });
    observer.observe(element);
    document.addEventListener('visibilitychange', updateVisibility);
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', updateVisibility);
    };
  }, []);

  useEffect(() => {
    if (!hasBrowser || !visible) return;
    const api = window.electronAPI;
    if (!api) return;
    const owner = `space-${session.id}-${crypto.randomUUID()}`;
    let active = true;
    let animationFrame = 0;
    let latestFrame: string | null = null;
    let revealed = false;
    const unsubscribe = api.on.sessionPreviewFrame((id, image) => {
      if (!active || id !== session.id) return;
      latestFrame = image;
      if (animationFrame) return;
      // Update the image once per display frame without re-rendering the card.
      // If multiple frames arrive, only the newest one needs decoding.
      animationFrame = requestAnimationFrame(() => {
        animationFrame = 0;
        if (!active || !latestFrame || !imageRef.current) return;
        imageRef.current.src = `data:image/jpeg;base64,${latestFrame}`;
        latestFrame = null;
        if (!revealed) {
          revealed = true;
          setHasFrame(true);
        }
      });
    });
    void api.sessions.previewStart(session.id, owner).catch((error) => {
      console.warn('[SpaceThumbnail] preview start failed', { sessionId: session.id, error });
    });
    return () => {
      active = false;
      if (animationFrame) cancelAnimationFrame(animationFrame);
      unsubscribe();
      void api.sessions.previewStop(session.id, owner).catch(() => {});
    };
  }, [session.id, hasBrowser, visible]);

  return (
    <div ref={thumbnailRef} className="space-card__thumbnail" aria-hidden="true">
      <div className="space-card__browser-bar">
        <span /><span /><span />
        <div className="space-card__address">{host}</div>
      </div>
      <img ref={imageRef} className="space-card__frame" hidden={!hasFrame} alt="" />
      {!hasFrame && (
        <div className="space-card__page-preview">
          <div className="space-card__placeholder">
            <svg width="30" height="26" viewBox="0 0 30 26" fill="none" aria-hidden="true">
              <rect x="2" y="3" width="26" height="20" rx="3" stroke="currentColor" strokeWidth="1.2" />
              <path d="M2 9h26" stroke="currentColor" strokeWidth="1.2" />
              <circle cx="6" cy="6" r=".75" fill="currentColor" />
              <circle cx="9" cy="6" r=".75" fill="currentColor" />
            </svg>
            <span>{hasPageAddress ? '页面未加载' : '暂无网页内容'}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function Space({ sessions, selectedId, onSelect }: SpaceProps): React.ReactElement {
  return (
    <section className="space-page" aria-label="我的空间">
      <div className="space-page__heading">
        <h1>我的空间</h1>
      </div>

      {sessions.length === 0 ? (
        <div className="space-page__empty">
          <svg className="space-page__empty-icon" width="48" height="48" viewBox="0 0 48 48" fill="none" aria-hidden="true">
            <path d="M14 10V8a4 4 0 0 1 4-4h20a4 4 0 0 1 4 4v22a4 4 0 0 1-4 4h-2" stroke="currentColor" strokeWidth="1.5" opacity="0.4" />
            <rect x="6" y="14" width="28" height="28" rx="5" stroke="currentColor" strokeWidth="1.5" />
            <path d="M6 23h28" stroke="currentColor" strokeWidth="1.5" />
            <circle cx="12" cy="18.5" r="1" fill="currentColor" />
            <circle cx="16" cy="18.5" r="1" fill="currentColor" />
          </svg>
          <p>还没有会话，从顶部开始一个新任务吧。</p>
        </div>
      ) : (
        <div className="space-grid">
          {sessions.map((session) => {
            const status = STATUS[session.status];
            const selected = session.id === selectedId;
            return (
              <div
                key={session.id}
                className={`space-card-shell${selected ? ' space-card-shell--selected' : ''}`}
              >
                <div className="space-card__title" title={session.prompt}>{session.prompt || '未命名会话'}</div>
                <button
                  type="button"
                  className={`space-card${selected ? ' space-card--selected' : ''}`}
                  aria-label={`打开会话：${session.prompt || '未命名会话'}`}
                  onClick={() => onSelect(session.id)}
                >
                  <SpaceThumbnail session={session} />
                </button>
                <div className={`space-card__status ${status.className}`}>
                  <i />{status.label}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default Space;
