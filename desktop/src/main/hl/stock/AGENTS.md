# Harness

You are running inside Browser Use Desktop. This directory is your working
directory.

## Browser Automation

Browser automation goes through `agent-browser`, driven by the agent itself
(`agent/react/browser.py`). The app hands over only a CDP port and a
target id; turning that target into an `agent-browser` tab is the agent's job.

Do not shell out to some other browser you find on PATH, and do not point a tool
straight at the app's CDP port. The views belong to live sessions, and driving
the wrong one acts on somebody else's page.

## Skills

Use `agent-skill search "<query>"` before reading files manually. It indexes
domain skills and user-created skills without dumping all skill content into
your context. After search, load the exact match with `agent-skill view <id>`.

Your provider prompt may include a compact skill index with ids, titles, and
short descriptions. Treat that as a menu of likely matches, not as full
instructions. Always load the skill body with `agent-skill view <id>` before
following it.

### Domain Skills

`./domain-skills/` contains site-specific playbooks. Before reasoning about a
specific website, check for a matching folder and read any relevant `.md` files
you find there. They document selectors, flows, rate limits, and gotchas that
are cheaper to reuse than to rediscover.

These files are read-only reference material and are overwritten on app launch.

### Skill Lifecycle

Create compact procedural skills under `./skills/` with `agent-skill create`
after a task succeeds and the new procedure is likely to repeat, long-running
enough to justify reuse, or generally applicable beyond the current session.
Good triggers include a complex task, a tricky error fix, trial and error that
changed the approach, or a user correction that should shape future work. As a
rough threshold, consider creating a skill after 5 or more meaningful tool
calls.

Do not create skills for one-off facts or calculations, temporary page state,
user-specific secrets, temporary tokens, private account details, speculative
or failed workflows, or content that is better as task output. Prefer updating
an existing skill with `agent-skill patch` when the new lesson belongs there.

Use `agent-skill delete <id>` only for local user-created skills that are wrong,
duplicative, or no longer useful. Do not delete stock domain skills.

Good skills include:

- when to use the skill
- numbered steps or exact commands
- pitfalls or failure modes
- verification steps that prove the skill worked

After writing or patching a skill, run:

```bash
agent-skill validate <id> --json
```

Keep skills small. Put bulky examples, scripts, templates, or assets in support
files only when the skill needs them.

## Harness Files

Do not edit `AGENTS.md`, `agent-skill/`, or `domain-skills/` as a first resort.

Only make a small harness edit when the user explicitly asks for it, or when a
confirmed bug or missing capability blocks the task. If you do edit a harness
file, say exactly what changed in your final answer.

## Uploads And Outputs

- Uploads from the user appear under `./uploads/<session_id>/`.
- Files you create for the user must go under `./outputs/<session_id>/`.
  Anything written there is shown to the user in the chat, so only put files
  there that they should see. Mention the filename in your final answer.

## Local App Diagnostics

If the user explicitly asks you to debug Browser Use Desktop, local app state is
one directory up from the harness:

- Runtime root: `..`
- Session database: `../sessions.db`
- Logs: `../logs/main.log`, `../logs/browser.log`, `../logs/renderer.log`,
  and `../logs/engine.log`
- Account state: `../account.json`
- Local task control: `../local-task-server.json`

For repo-level local development, do not assume the platform default profile.
Coding agents should use the repo `AGENTS.md` and `task worktree:profile:path`
to keep `sessions.db` and `local-task-server.json` aligned for the active
worktree.

Do not print raw credentials, tokens, keychain values, or the local task bearer
token. Use status checks and masked values.

## Done

Say what you accomplished when the task is complete. Keep it short and
user-facing.
