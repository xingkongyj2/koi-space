import React from 'react';
import { MemoryIndicator } from './MemoryIndicator';

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
          >想让小K帮你干什么？</button>
        )}
        {!isSpace && (
          <button type="button" className="hub-navbar__nav-button" onClick={onGoSpace}>我的空间</button>
        )}
      </nav>
      <div className="hub-navbar__right">
        {zoomFactor !== 1.0 && (
          <button className="hub-navbar__zoom" onClick={onResetZoom} title={resetZoomTitle}>
            {Math.round(zoomFactor * 100)}%
          </button>
        )}
        <MemoryIndicator />
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
