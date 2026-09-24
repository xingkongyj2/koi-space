const IGNORED_RENDERER_MESSAGE_FRAGMENTS = [
  'ResizeObserver loop limit exceeded',
  'ResizeObserver loop completed with undelivered notifications',
];

export function isIgnorableRendererMessage(message: unknown): boolean {
  if (typeof message !== 'string') return false;
  return IGNORED_RENDERER_MESSAGE_FRAGMENTS.some((fragment) => message.includes(fragment));
}

export function isIgnorableRendererLog(message: unknown, extra: unknown): boolean {
  if (isIgnorableRendererMessage(message)) return true;
  if (!extra || typeof extra !== 'object') return false;
  return isIgnorableRendererMessage((extra as { message?: unknown }).message);
}
