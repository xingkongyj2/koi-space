'use client';

import ParticleWave from './ParticleWave';

function SparklesIcon() {
  return (
    <svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m12 3-1.7 5.3L5 10l5.3 1.7L12 17l1.7-5.3L19 10l-5.3-1.7L12 3Z" />
      <path d="m19 16-.8 2.2L16 19l2.2.8L19 22l.8-2.2L22 19l-2.2-.8L19 16Z" />
    </svg>
  );
}

function SquareIcon() {
  return <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="1" /></svg>;
}

export type AiControlEffectProps = {
  /** Controls both the particle field and the control notice. */
  enabled?: boolean;
  /** Keep the particle field visible while hiding the notice. */
  showParticles?: boolean;
  /** Keep the notice visible while hiding the particle field. */
  showBanner?: boolean;
  /** Main status text shown in the notice. */
  title?: string;
  /** Small context text shown below the title. */
  subtitle?: string;
  /** Label for the stop action. */
  stopLabel?: string;
  /** Hide the stop action when the parent owns the lifecycle elsewhere. */
  showStopButton?: boolean;
  /** Called when the user presses the stop action. */
  onStop?: () => void;
  /** Optional class applied to the notice. */
  className?: string;
};

/**
 * Full-screen AI control effect.
 *
 * The component is intentionally independent of the workflow page: it owns
 * the canvas animation and status UI, while the parent owns the active state.
 */
export default function AiControlEffect({
  enabled = true,
  showParticles = true,
  showBanner = true,
  title = 'AI 正在控制',
  subtitle = '本地演示',
  stopLabel = '停止控制',
  showStopButton = true,
  onStop,
  className = '',
}: AiControlEffectProps) {
  if (!enabled) return null;

  const bannerVisible = showBanner;
  const particlesVisible = showParticles;
  if (!bannerVisible && !particlesVisible) return null;

  return (
    <>
      {particlesVisible && <ParticleWave />}
      {bannerVisible && (
        <section className={`ai-control-notice ${className}`.trim()} aria-label="AI 控制提示">
          <div className="ai-notice-icon" aria-hidden="true">
            <SparklesIcon />
          </div>
          <div className="ai-notice-copy" role="status" aria-live="polite">
            <strong>{title}</strong>
            <span><i aria-hidden="true" />{subtitle}</span>
          </div>
          {showStopButton && (
            <button className="ai-stop-control" type="button" onClick={onStop}>
              <SquareIcon />
              {stopLabel}
            </button>
          )}
        </section>
      )}
    </>
  );
}

export { ParticleWave };
