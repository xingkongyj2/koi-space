/**
 * Prompt lines that introduce the browser tool to an agent.
 *
 * Shared by every engine adapter — the browser contract is identical across
 * Claude Code, Codex, and BrowserCode, so the prose lives here rather than in
 * three copies that drift apart.
 */

export function browserToolPromptLines(): string[] {
  return [
    'You are driving a specific Chromium browser view on this machine.',
    'Use the `agent-browser` CLI for every browser action. It is already connected and already bound to your view — run its commands directly.',
    'Read `./AGENTS.md` for the command surface, the verification loop, and the screenshot/outputs conventions.',
    'Never run `agent-browser connect`, `agent-browser tab`, or pass `--session`/`--cdp`. Binding is managed for you; re-selecting a tab will point you at a different session\'s browser.',
    'If a command reports that the session lost its browser binding, stop and tell the user — do not work around it by connecting to another browser.',
    'Do not edit harness files unless the user asks or a confirmed agent-browser defect blocks the task.',
  ];
}
