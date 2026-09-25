# Browser Use Desktop Agent Notes

## Local Dev Profiles

- The app's runtime database lives under Electron `userData`, not under the git
  worktree. Do not assume the default profile when working across branches.
- Use `task worktree:profile:path` to get this branch's isolated profile path.
  It resolves to `.task/user-data/<current-branch>` by default.
- Use `task worktree:up` to start the app against that isolated profile.
- Use `task worktree:profile:clean FORCE=1` to delete this branch's isolated
  profile after quitting the app.
- Use `task db:worktree:copy FROM=default` to copy `sessions.db` from the
  platform default profile into this branch profile. Pass `FROM=branch:<name>`
  to copy from another branch profile, including branch names with `/`. Use an
  absolute path, `./relative/path`, `~/path`, or `FROM=path:<path>` for explicit
  filesystem paths. Pass `FORCE=1` if the target DB already exists.
- Use `task db:worktree:doctor` after copying or when local task runs fail. It
  compares the target `sessions.db` schema version and schema ID against the
  current checkout's `src/main/sessions/schema-manifest.json`.
- If the app was launched with `AGB_USER_DATA_DIR`, run `task agent:run` with
  the same `AGB_USER_DATA_DIR`. The local task runner reads
  `<userData>/local-task-server.json`; pointing the CLI at a different profile
  will miss the running app even if `sessions.db` is valid.
- Quit the app before copying or cleaning profile files. SQLite WAL files and
  Electron runtime files can be open while the app is running.

## Session Schema Changes

- `DB_SCHEMA_VERSION` gates migrations at runtime.
- `src/main/sessions/schema-manifest.json` tracks the expected fresh schema ID
  for CI/main drift detection.
- Run `task db:schema:check` after touching `SessionDb` migrations.
- Run `task db:schema:update` only after an intentional schema change.

## Session Lifecycle

- Stop (`SessionManager.cancelSession`) keeps the session in the hub as
  `stopped` and resumable — its card offers Resume and a follow-up box.
- Close (`sessions:delete`) removes it outright: cancels a live run, tears down
  the browser view, deletes the DB row, and emits `session-removed`, which both
  renderer stores (`useSessionsQuery` cache and `sessionsStore`) honour. Nothing
  reloads it on the next launch. Do not reintroduce a soft-close that only flips
  status — that leaves cards the user cannot get rid of.

## Python Agent

- The only engine is `python`, implemented in `app/python/koi_agent/`. The app
  spawns `python -u -m koi_agent` per task, writes one JSON envelope to stdin, and
  reads NDJSON `HlEvent`s from stdout (stderr is diagnostics only — never write
  protocol data there).
- **Browser control lives entirely in Python.** The app resolves the session's
  CDP `targetId` and passes it with the port in the envelope; that is the whole
  of its browser knowledge. `koi_agent/browser.py` owns agent-browser, tab
  binding and rebinding, and `koi_agent/cdp.py` is a dependency-free CDP client.
  Do not add agent-browser calls to the Electron side.
- agent-browser addresses pages as `t1..tN` and has no notion of a CDP targetId,
  so binding means translating one to the other. Never let it guess: an unmatched
  or ambiguous target must raise, because picking the wrong tab drives another
  session's page.
- The interpreter is resolved in this order: `KOI_AGENT_PYTHON`, then
  `app/python/.venv/`, then `python3` on the enriched PATH. `KOI_AGENT_DIR`
  overrides the package location.
- Run `task python:test` after touching `app/python/` — stdlib `unittest`, no
  dependencies.
- There is no engine picker in the UI and no provider-credential plumbing
  anywhere: an agent brings its own model configuration. The adapter registry
  (`app/src/main/hl/engines/registry.ts`) still accepts more engines.

