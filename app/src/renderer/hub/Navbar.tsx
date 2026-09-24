import React from 'react';
import { MemoryIndicator } from './MemoryIndicator';

interface NavbarProps {
  isDashboard: boolean;
  onGoDashboard: () => void;
  onOpenSettings: () => void;
  settingsShortcut?: string;
  zoomFactor: number;
  onResetZoom: () => void;
  resetZoomTitle?: string;
}

export function Navbar({
  onOpenSettings,
  settingsShortcut,
  zoomFactor,
  onResetZoom,
  resetZoomTitle,
}: NavbarProps): React.ReactElement {
  return (
    <header className="hub-navbar">
      <div className="hub-navbar__left">
        <MemoryIndicator onOpenSettings={onOpenSettings} settingsShortcut={settingsShortcut} />
      </div>
      <div className="hub-navbar__center" />
      <div className="hub-navbar__right">
        {zoomFactor !== 1.0 && (
          <button className="hub-navbar__zoom" onClick={onResetZoom} title={resetZoomTitle}>
            {Math.round(zoomFactor * 100)}%
          </button>
        )}
      </div>
    </header>
  );
}

export default Navbar;
