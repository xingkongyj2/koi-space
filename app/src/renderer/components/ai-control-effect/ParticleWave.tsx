'use client';

import { useEffect, useRef } from 'react';
import './ai-control-effect.css';

/** A transparent, viewport-sized effect. All pointer events pass through it. */
export default function ParticleWave({ className = '', showGlow = true }: { className?: string; showGlow?: boolean }) {
  const particlesRef = useRef<HTMLCanvasElement>(null);
  const glowRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const particles = particlesRef.current;
    const glow = glowRef.current;
    if (!particles) return;
    const ctx = particles.getContext('2d');
    const rim = showGlow ? glow?.getContext('2d') : null;
    if (!ctx) return;

    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let width = 0;
    let height = 0;
    let frame = 0;
    let lastFrame = -Infinity;
    let elapsed = 0;
    let previousTime = 0;
    let points: { x: number; y: number; edge: number; radius: number; directionX: number; directionY: number }[] = [];
    const buckets: number[][] = Array.from({ length: 20 }, () => []);

    // Each mount has a new field. Smooth interpolation avoids frame-to-frame flicker.
    const seed = Math.random() * 10000;
    const ease = (v: number) => v * v * (3 - 2 * v);
    function noise(x: number, y: number) {
      const ix = Math.floor(x), iy = Math.floor(y);
      const fx = ease(x - ix), fy = ease(y - iy);
      const hash = (a: number, b: number) => {
        const n = Math.sin(a * 127.1 + b * 311.7 + seed) * 43758.5453;
        return n - Math.floor(n);
      };
      const low = hash(ix, iy) * (1 - fx) + hash(ix + 1, iy) * fx;
      const high = hash(ix, iy + 1) * (1 - fx) + hash(ix + 1, iy + 1) * fx;
      return low * (1 - fy) + high * fy;
    }

    function resize() {
      width = particles!.parentElement?.clientWidth || window.innerWidth;
      height = particles!.parentElement?.clientHeight || window.innerHeight;
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      particles!.width = Math.round(width * ratio);
      particles!.height = Math.round(height * ratio);
      ctx!.setTransform(ratio, 0, 0, ratio, 0, 0);
      // The soft glow needs fewer pixels than the crisp particle layer.
      if (glow && rim) {
        glow.width = Math.ceil(width / 2);
        glow.height = Math.ceil(height / 2);
        rim.setTransform(0.5, 0, 0, 0.5, 0, 0);
      }
      const spacing = Math.max(10, Math.sqrt(width * height / 14000));
      points = [];
      for (let y = spacing / 2; y < height; y += spacing) {
        for (let x = spacing / 2; x < width; x += spacing) {
          const distance = Math.min(x, y, width - x, height - y);
          const dx = x - width / 2;
          const dy = y - height / 2;
          const radius = Math.hypot(dx, dy);
          points.push({ x, y, edge: Math.exp(-distance / 110), radius,
            directionX: radius > 0 ? dx / radius : 0,
            directionY: radius > 0 ? dy / radius : 0 });
        }
      }
      paint(elapsed);
    }

    function paint(time: number) {
      if (!ctx) return;
      ctx.clearRect(0, 0, width, height);
      rim?.clearRect(0, 0, width, height);

      // Opposite edges share expansion space, keeping the center visually open.
      // No orbit, fixed pulse period, constant-width stroke, or saturated outline.
      const maxDepth = Math.min(220, Math.min(width, height) * 0.30);
      const edgeTime = time * 0.38;
      // Complementary pairs prevent top/bottom or left/right swelling together.
      const verticalBias = ease(noise(12.4, edgeTime * 0.82));
      const horizontalBias = ease(noise(53.7, edgeTime * 0.76 + 11));
      const verticalBudget = 0.5 + 0.5 * noise(89.2, edgeTime * 0.52 + 23);
      const horizontalBudget = 1.5 - verticalBudget;
      const edgeStrength = [
        (0.15 + 0.85 * verticalBias) * verticalBudget,
        (0.15 + 0.85 * horizontalBias) * horizontalBudget,
        (0.15 + 0.85 * (1 - verticalBias)) * verticalBudget,
        (0.15 + 0.85 * (1 - horizontalBias)) * horizontalBudget,
      ];
      for (let side = 0; rim && side < 4; side++) {
        const length = side % 2 === 0 ? width : height;
        const profile: { along: number; depth: number }[] = [];
        for (let along = -32; along <= length + 32; along += 16) {
          const broad = noise(along / 285 + side * 29, edgeTime * 1.05 + side * 7);
          const detail = noise(along / 105 + side * 43, edgeTime * 1.65 + 40);
          const value = broad * 0.75 + detail * 0.25;
          const swell = ease(Math.max(0, Math.min(1, (value - 0.24) / 0.49)));
          // Corners carry the recognizable blue accent; middle edges stay restrained.
          const cornerDistance = Math.min(Math.max(0, along), Math.max(0, length - along));
          const cornerWeight = 0.52 + 0.86 * Math.exp(-((cornerDistance / Math.min(320, length * 0.38)) ** 2));
          const depth = 7 + maxDepth * (0.30 + 0.70 * swell) * edgeStrength[side] * cornerWeight;
          profile.push({ along, depth });
        }
        // Layer translucent, pale ribbons into a feathered gradient.
        for (let layer = 0; layer < 24; layer++) {
          const fraction = 1 - layer / 24;
          const point = (along: number, inward: number) => {
            if (side === 0) return { x: along, y: inward };
            if (side === 1) return { x: width - inward, y: along };
            if (side === 2) return { x: along, y: height - inward };
            return { x: inward, y: along };
          };
          rim.beginPath();
          let p = point(-32, -32);
          rim.moveTo(p.x, p.y);
          p = point(length + 32, -32);
          rim.lineTo(p.x, p.y);
          for (let i = profile.length - 1; i >= 0; i--) {
            p = point(profile[i].along, profile[i].depth > 0 ? profile[i].depth * fraction : profile[i].depth);
            rim.lineTo(p.x, p.y);
          }
          rim.closePath();
          rim.fillStyle = `rgba(72, 120, 226, ${0.040 + (1 - fraction) * 0.025})`;
          rim.fill();
        }
      }

      for (const bucket of buckets) bucket.length = 0;
      // Broad, softly blended waves originate at the center without sharp rings.
      const wavelength = Math.max(240, Math.min(420, Math.min(width, height) * 0.48));
      const speed = 165;
      const maxRadius = Math.hypot(width / 2, height / 2);
      for (const p of points) {
        const phase = (p.radius - time * speed) / wavelength * Math.PI * 2;
        // Blend two broad radial waves, as a gentle swell rather than a bright crest.
        const swell = 0.5 + 0.32 * Math.sin(phase) + 0.18 * Math.sin(phase * 0.61 + 1.2);
        const crest = Math.pow(Math.max(0, swell), 1.35);
        const falloff = 1 - 0.32 * p.radius / maxRadius;
        const wave = crest * falloff;
        const opacity = Math.min(0.74, 0.08 + wave * 0.62 + p.edge * 0.045);
        const index = Math.min(19, Math.floor(opacity / 0.8 * 20));
        // Subtle radial movement keeps the original airy particle-field character.
        const displacement = Math.sin(phase) * (1.0 + crest * 2.3) * falloff;
        buckets[index].push(
          p.x + p.directionX * displacement,
          p.y + p.directionY * displacement,
          1.4 + wave * 1.0,
        );
      }
      // Batch particles by brightness instead of changing fill style for every dot.
      for (let i = 0; i < buckets.length; i++) {
        const bucket = buckets[i];
        ctx.fillStyle = `rgba(216, 228, 249, ${(i + 0.5) / 20 * 0.8})`;
        ctx.beginPath();
        for (let j = 0; j < bucket.length; j += 3) {
          const size = bucket[j + 2];
          ctx.rect(bucket[j] - size / 2, bucket[j + 1] - size / 2, size, size);
        }
        ctx.fill();
      }
    }

    function animate(now: number) {
      if (document.hidden || motion.matches) { frame = 0; return; }
      if (now - lastFrame >= 1000 / 40) {
        if (previousTime) elapsed += Math.min((now - previousTime) / 1000, 0.08);
        previousTime = now;
        lastFrame = now;
        paint(elapsed);
      }
      frame = requestAnimationFrame(animate);
    }
    function resume() {
      cancelAnimationFrame(frame);
      frame = 0;
      previousTime = 0;
      lastFrame = -Infinity;
      if (!document.hidden && !motion.matches) frame = requestAnimationFrame(animate);
      else if (!document.hidden) paint(elapsed);
    }
    resize();
    resume();
    const observer = new ResizeObserver(resize);
    if (particles.parentElement) observer.observe(particles.parentElement);
    window.addEventListener('resize', resize);
    document.addEventListener('visibilitychange', resume);
    motion.addEventListener('change', resume);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', resize);
      document.removeEventListener('visibilitychange', resume);
      motion.removeEventListener('change', resume);
    };
  }, [showGlow]);

  return <div className={`particle-wave ${className}`.trim()} aria-hidden="true">
    {showGlow && <canvas ref={glowRef} className="particle-wave-glow" />}
    <canvas ref={particlesRef} className="particle-wave-dots" />
  </div>;
}
