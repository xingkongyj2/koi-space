import ParticleWave from '../components/ai-control-effect/ParticleWave';
import './PagePreparing.css';

export function PagePreparing(): React.ReactElement {
  return (
    <div className="page-preparing" role="status" aria-live="polite">
      <ParticleWave showGlow={false} className="page-preparing__particles" />
      <span className="page-preparing__label">网页准备中</span>
    </div>
  );
}
