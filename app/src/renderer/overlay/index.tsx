import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AiControlEffect } from '@/renderer/components/ai-control-effect';
import type { TakeoverModeState } from '@/preload/overlay';
import './overlay.css';

declare global {
  interface Window {
    takeoverAPI?: {
      sessionId: string;
      onMode: (cb: (state: TakeoverModeState) => void) => () => void;
      stop: () => Promise<unknown>;
    };
  }
}

const IDLE_SUBTITLE = 'Koi Agent';

function Overlay(): React.ReactElement {
  const [state, setState] = useState<TakeoverModeState>({ mode: 'idle', subtitle: '' });

  useEffect(() => {
    document.body.dataset.mode = state.mode;
  }, [state.mode]);

  useEffect(() => {
    const api = window.takeoverAPI;
    if (!api?.onMode) return;
    return api.onMode(setState);
  }, []);

  const active = state.mode === 'active';

  return (
    <>
      {!active && <div className="overlay-idle-label">Browser not started yet</div>}
      <AiControlEffect
        enabled={active}
        title="AI 正在控制"
        subtitle={state.subtitle || IDLE_SUBTITLE}
        stopLabel="停止控制"
        onStop={() => {
          void window.takeoverAPI?.stop();
        }}
      />
    </>
  );
}

const container = document.getElementById('overlay-root');
if (container) createRoot(container).render(<Overlay />);
