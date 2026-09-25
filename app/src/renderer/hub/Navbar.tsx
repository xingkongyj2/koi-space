import React from 'react';
import { MemoryIndicator } from './MemoryIndicator';
import brandLogo from './bu-logo-white.svg';

interface NavbarProps {
  isDashboard: boolean;
  isBrowser: boolean;
  onGoDashboard: () => void;
  onGoBrowser: () => void;
  onOpenHelp: () => void;
  onOpenSettings: () => void;
  settingsShortcut?: string;
  zoomFactor: number;
  onResetZoom: () => void;
  resetZoomTitle?: string;
}

export function Navbar({
  isDashboard,
  isBrowser,
  onGoDashboard,
  onGoBrowser,
  onOpenHelp,
  onOpenSettings,
  settingsShortcut,
  zoomFactor,
  onResetZoom,
  resetZoomTitle,
}: NavbarProps): React.ReactElement {
  return (
    <header className="hub-navbar">
      <div className="hub-navbar__left">
        <button type="button" className="hub-navbar__logo" aria-label="Go to home" onClick={onGoDashboard}>
          <img src={brandLogo} alt="" />
        </button>
        <button type="button" className={`hub-navbar__nav-button${isDashboard ? ' hub-navbar__nav-button--active' : ''}`}
          aria-current={isDashboard ? 'page' : undefined} onClick={onGoDashboard}>Workspace</button>
        <button type="button" className={`hub-navbar__nav-button${isBrowser ? ' hub-navbar__nav-button--active' : ''}`}
          aria-current={isBrowser ? 'page' : undefined} onClick={onGoBrowser}>Browser</button>
        <button type="button" className="hub-navbar__nav-button" onClick={onOpenHelp}>Help</button>
      </div>
      <div className="hub-navbar__center" aria-label="Browser Use workspace">
        <span className="hub-navbar__document-name">Browser Use</span>
      </div>
      <div className="hub-navbar__right">
        {zoomFactor !== 1.0 && (
          <button className="hub-navbar__zoom" onClick={onResetZoom} title={resetZoomTitle}>
            {Math.round(zoomFactor * 100)}%
          </button>
        )}
        <MemoryIndicator />
        <button type="button" className="hub-navbar__settings-button"
          onClick={onOpenSettings} title={settingsShortcut ? `Settings (${settingsShortcut})` : 'Settings'}>
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M6.3 1.6a1.7 1.7 0 0 1 3.4 0l.1.5a1.7 1.7 0 0 0 2.1 1.2l.5-.2a1.7 1.7 0 0 1 2.1 2.9l-.4.4a1.7 1.7 0 0 0 0 2.4l.4.4a1.7 1.7 0 0 1-2.1 2.9l-.5-.2a1.7 1.7 0 0 0-2.1 1.2l-.1.5a1.7 1.7 0 0 1-3.4 0l-.1-.5a1.7 1.7 0 0 0-2.1-1.2l-.5.2a1.7 1.7 0 0 1-2.1-2.9l.4-.4a1.7 1.7 0 0 0 0-2.4l-.4-.4a1.7 1.7 0 0 1 2.1-2.9l.5.2a1.7 1.7 0 0 0 2.1-1.2l.1-.5Z" stroke="currentColor" strokeWidth="1.2"/>
            <circle cx="8" cy="7.6" r="2" stroke="currentColor" strokeWidth="1.2"/>
          </svg>
          <span>Settings</span>
        </button>
      </div>
    </header>
  );
}

export default Navbar;
