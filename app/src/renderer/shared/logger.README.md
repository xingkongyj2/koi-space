# renderer/shared/logger.ts

Structured logger for the renderer. Replaces ad-hoc `console.*` calls
with a per-module namespaced logger and forwards warn/error to the
main-process `renderer` channel logger so production errors land on
disk instead of evaporating with DevTools.

## Usage

```ts
import { makeLogger } from '@/renderer/shared/logger';

const log = makeLogger('EnginePicker');

log.info('refreshStatus.request', { ids });
log.warn('refreshStatus.failed', { id, error: (err as Error).message });
log.error('engineInstall failed', err);          // Errors expand to {error, stack, name}
```

Levels: `debug | info | warn | error`. Second arg is a structured
extras object — pass an `Error` and it expands into `{error, stack, name}`.

## Console formatting

```
[EnginePicker] refreshStatus.request {ids: ['codex', 'claude-code']}
[EnginePicker] engineInstall failed {error: '...', stack: '...', name: 'Error'}
```

Same `[Module]` prefix the renderer already uses across the codebase.

## On-disk output

`warn` and `error` are forwarded to main and written as JSONL to:
```
<userData>/logs/renderer.log
```
Each line looks like:
```json
{"ts":"2026-05-18T20:11:33.421Z","level":"error","channel":"renderer","msg":"engineInstall failed","ns":"EnginePicker","error":"...","stack":"..."}
```

## Modes (per-renderer-window, via localStorage)

| Mode | Console | Disk forward |
|---|---|---|
| `normal` (default) | all levels | warn + error |
| `verbose` | all levels | info + warn + error |
| `silent` | nothing | warn + error |

Toggle in DevTools:
```js
localStorage.setItem('rendererLog', 'verbose');
// or 'silent' or remove entirely for 'normal'
```

Mode is re-read on every call so toggling takes effect without a reload.

## Migration recipe (for the other ~110 call sites)

```diff
+ import { makeLogger } from '@/renderer/shared/logger';
+ const log = makeLogger('MyComponent');

- console.info('[MyComponent] foo.request', { id });
+ log.info('foo.request', { id });

- console.warn('[MyComponent] foo.failed', { id, error });
+ log.warn('foo.failed', { id, error });

- console.error('[MyComponent] crashed', err);
+ log.error('crashed', err);
```

That's it — same prefix in console, plus on-disk persistence for warn/error.

## What still uses raw `console.*`

About 110 call sites scattered across the renderer were intentionally
left as-is in the initial cutover. Migrate opportunistically — the two
biggest wins (global error handlers and EnginePicker) are already
done. There's no global lint rule forcing migration; the two patterns
coexist safely.
