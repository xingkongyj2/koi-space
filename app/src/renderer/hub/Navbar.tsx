import React from 'react';

interface NavbarProps {
  isDashboard: boolean;
  isSpace: boolean;
  onGoSpace: () => void;
  onOpenSettings: () => void;
  onNewSession: () => void;
  settingsShortcut?: string;
  zoomFactor: number;
  onResetZoom: () => void;
  resetZoomTitle?: string;
}

export function Navbar({
  isDashboard,
  isSpace,
  onGoSpace,
  onOpenSettings,
  onNewSession,
  settingsShortcut,
  zoomFactor,
  onResetZoom,
  resetZoomTitle,
}: NavbarProps): React.ReactElement {
  return (
    <header className="hub-navbar hub-navbar--centered">
      <nav className="hub-navbar__navigation" aria-label="主导航">
        {!isDashboard && (
          <button
            type="button"
            className="hub-navbar__task-entry"
            onClick={onNewSession}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M8 2.5v11M2.5 8h11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <span>新任务</span>
          </button>
        )}
        {!isSpace && (
          <button type="button" className="hub-navbar__nav-button" onClick={onGoSpace}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <rect x="1.75" y="1.75" width="5.25" height="5.25" rx="1.25" stroke="currentColor" strokeWidth="1.3" />
              <rect x="9" y="1.75" width="5.25" height="5.25" rx="1.25" stroke="currentColor" strokeWidth="1.3" />
              <rect x="1.75" y="9" width="5.25" height="5.25" rx="1.25" stroke="currentColor" strokeWidth="1.3" />
              <rect x="9" y="9" width="5.25" height="5.25" rx="1.25" stroke="currentColor" strokeWidth="1.3" />
            </svg>
            <span>我的空间</span>
          </button>
        )}
      </nav>
      <div className="hub-navbar__right">
        {zoomFactor !== 1.0 && (
          <button className="hub-navbar__zoom" onClick={onResetZoom} title={resetZoomTitle}>
            {Math.round(zoomFactor * 100)}%
          </button>
        )}
        <button type="button" className="hub-navbar__settings-button"
          onClick={onOpenSettings} aria-label="设置" title={settingsShortcut ? `设置 (${settingsShortcut})` : '设置'}>
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M6.3 1.6a1.7 1.7 0 0 1 3.4 0l.1.5a1.7 1.7 0 0 0 2.1 1.2l.5-.2a1.7 1.7 0 0 1 2.1 2.9l-.4.4a1.7 1.7 0 0 0 0 2.4l.4.4a1.7 1.7 0 0 1-2.1 2.9l-.5-.2a1.7 1.7 0 0 0-2.1 1.2l-.1.5a1.7 1.7 0 0 1-3.4 0l-.1-.5a1.7 1.7 0 0 0-2.1-1.2l-.5.2a1.7 1.7 0 0 1-2.1-2.9l.4-.4a1.7 1.7 0 0 0 0-2.4l-.4-.4a1.7 1.7 0 0 1 2.1-2.9l.5.2a1.7 1.7 0 0 0 2.1-1.2l.1-.5Z" stroke="currentColor" strokeWidth="1.2"/>
            <circle cx="8" cy="7.6" r="2" stroke="currentColor" strokeWidth="1.2"/>
          </svg>
          <span>设置</span>
        </button>
      </div>
    </header>
  );
}

export default Navbar;
