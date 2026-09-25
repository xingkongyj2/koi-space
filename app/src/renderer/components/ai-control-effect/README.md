# AiControlEffect

`AiControlEffect` packages the full-screen AI control visual used by the demo:

- a center-out particle wave canvas;
- the soft blue edge glow;
- the bottom status banner and red stop action.

Copy the `components/ai-control-effect` directory into another React app, then import the component and its stylesheet through `AiControlEffect.tsx`:

```tsx
import { AiControlEffect } from '@/renderer/components/ai-control-effect';

export function Screen({ controlling, workflowName, stop }: Props) {
  return (
    <AiControlEffect
      enabled={controlling}
      title="AI 正在控制"
      subtitle={`${workflowName} · 本地演示`}
      stopLabel="停止控制"
      onStop={stop}
    />
  );
}
```

`enabled` controls the whole module. Use `showParticles={false}` or `showBanner={false}` when only one layer is needed. The component is pointer-transparent so it can sit above an existing page without blocking controls.
