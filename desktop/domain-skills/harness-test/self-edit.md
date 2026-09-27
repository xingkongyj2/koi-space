# Legacy Harness Self-Edit Test

Historical notes about the harness self-edit escape hatch. This is not runtime
guidance for app-spawned agents — `AGENTS.md` is.

Browser automation runs through `agent-browser`, an external CLI the app binds
to the session's view before the agent starts. Treat harness edits as an escape
hatch only when the user explicitly asks for them, or when a confirmed defect
blocks the task.

## Files that matter

- `AGENTS.md` — the app-specific harness manual.
- `agent-browser-shim/` — the PATH shim that keeps the session bound to its own
  browser view; app launches replace it.
- `domain-skills/` — site playbooks, synced from upstream; app launches replace it.

## Legacy notes

Two earlier browser runtimes have been removed from the desktop app: the
`helpers.js` + `TOOLS.json` dispatcher, and the vendored `browser-harness-js`
CDP REPL (plus its `interaction-skills/` recipes). Do not revive either for
ordinary browser tasks — `agent-browser skills get core --full` covers browser
mechanics now.

## If an edit is unavoidable

- Keep the patch minimal and task-scoped.
- Prefer fixing app source stock files over editing generated userData copies.
- Mention the edited file and reason in the final answer.
