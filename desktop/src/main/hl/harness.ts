/**
 * Harness directory bootstrap: seeds `<userData>/harness/` with the
 * provider-neutral agent-skill CLI, domain skills, and the app-specific
 * AGENTS.md.
 *
 * Browser control is not bootstrapped here: the Python agent owns agent-browser
 * end to end (see `agent/react/browser.py`) and the app only hands it a
 * CDP port and target id.
 *
 * Stock content is bundled via Vite's `?raw` import modifier.
 *
 * Domain skills (`./stock/domain-skills/`) are separate, read-only reference
 * folders. They are fully re-materialized on every launch — the agent consults
 * them but must not edit them (upgrades will clobber any changes).
 *
 * User-created skills live under `<userData>/harness/skills/` and are
 * persistent. They are never replaced by bootstrap.
 */

import fs from 'node:fs';
import path from 'node:path';
import { app } from 'electron';
import { mainLogger } from '../logger';

import STOCK_SKILL_MD from './stock/AGENTS.md?raw';

// Bundled domain-skills tree. Vite eagerly inlines every file under
// stock/domain-skills/ as a raw string at build time. Keys are the full
// module path; strip the prefix to get the in-tree relative path.
const DOMAIN_SKILLS_PREFIX = './stock/domain-skills/';
const STOCK_DOMAIN_SKILLS = import.meta.glob('./stock/domain-skills/**/*', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;
const AGENT_SKILL_PREFIX = './stock/agent-skill/';
const STOCK_AGENT_SKILL = import.meta.glob('./stock/agent-skill/**/*', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

export function harnessDir(): string {
  return path.join(app.getPath('userData'), 'harness');
}

export function toolsPath(): string { return path.join(harnessDir(), 'TOOLS.json'); }
export function skillPath(): string { return path.join(harnessDir(), 'AGENTS.md'); }
export function domainSkillsDir(): string { return path.join(harnessDir(), 'domain-skills'); }
export function agentSkillDir(): string { return path.join(harnessDir(), 'agent-skill'); }
export function userSkillsDir(): string { return path.join(harnessDir(), 'skills'); }

/**
 * Skill IDs are `<domain>/<topic>` strings (e.g. `user/fun/page-word-count`,
 * `domain/github/repo`, `interaction/cookies`). The on-disk layout differs per
 * domain - these helpers are the single source of truth for the conversion so
 * the engine post-processor and any IPC handler stay aligned.
 */
export interface SkillId { domain: string; topic: string }

function sanitizeSkillTopic(rawTopic: string): string | null {
  const topic = rawTopic.trim().replace(/^['"]+|['"]+$/g, '').replace(/\.md$/i, '').replace(/\\/g, '/');
  if (!topic || topic.startsWith('/') || path.isAbsolute(rawTopic) || path.isAbsolute(topic)) return null;
  const segments = topic.split('/');
  if (segments.some((segment) => !segment || segment === '.' || segment === '..' || /^[A-Za-z]:/.test(segment))) {
    return null;
  }
  return segments.join('/');
}

export function skillPathFromMeta(meta: SkillId, rootDir: string = harnessDir()): string | null {
  const topic = sanitizeSkillTopic(meta.topic);
  if (!topic) return null;
  if (meta.domain === 'user') return path.join(rootDir, 'skills', topic, 'SKILL.md');
  if (meta.domain === 'domain') return path.join(rootDir, 'domain-skills', topic + '.md');
  if (meta.domain === 'interaction') return path.join(rootDir, 'interaction-skills', topic + '.md');
  return null;
}

export function skillMetaFromPath(resolved: string, rootDir: string = harnessDir()): SkillId | null {
  const rel = path.relative(rootDir, resolved).split(path.sep).join('/');
  if (rel.startsWith('domain-skills/') && rel.endsWith('.md')) {
    return { domain: 'domain', topic: rel.slice('domain-skills/'.length, -'.md'.length) };
  }
  if (rel.startsWith('interaction-skills/') && rel.endsWith('.md')) {
    return { domain: 'interaction', topic: rel.slice('interaction-skills/'.length, -'.md'.length) };
  }
  if (rel.startsWith('skills/') && rel.endsWith('/SKILL.md')) {
    return { domain: 'user', topic: rel.slice('skills/'.length, -'/SKILL.md'.length) };
  }
  return null;
}

export function skillIdToPath(skillId: string, rootDir: string = harnessDir()): string | null {
  const cleaned = skillId.trim().replace(/^['"]+|['"]+$/g, '').replace(/\\/g, '/');
  if (!cleaned || cleaned.startsWith('--') || cleaned.startsWith('/') || path.isAbsolute(skillId) || path.isAbsolute(cleaned)) return null;
  const parts = cleaned.split('/');
  if (parts.some((part) => !part)) return null;
  if (parts.length < 2) return null;
  const [domain, ...rest] = parts;
  return skillPathFromMeta({ domain, topic: rest.join('/') }, rootDir);
}

/**
 * Ensure `<userData>/harness/` exists and contains the stock files.
 * - Writes AGENTS.md if missing or stale.
 * - Removes stale TOOLS.json from the legacy dispatcher path, and the legacy
 *   helpers.js / browser-harness-js / interaction-skills trees that agent-browser
 *   replaced.
 * - Fully replaces the agent-skill CLI and domain skills from the bundle.
 * Manual edits to an up-to-date AGENTS.md are preserved as an escape hatch.
 */
export function bootstrapHarness(): void {
  const dir = harnessDir();
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch (err) {
    mainLogger.error('harness.bootstrap.mkdir.failed', { dir, error: (err as Error).message });
    throw err;
  }

  const sp = skillPath();
  // Staleness marker bumps force a one-time rewrite of AGENTS.md for
  // existing users. AGENTS.md is the harness manual, not agent-editable
  // state — safe to overwrite so new sections (domain-skills, etc.) land
  // without the user deleting their userData.
  const sentinel = 'agent/react/browser.py';
  const needsSkill = !fs.existsSync(sp) || (() => {
    try { return !fs.readFileSync(sp, 'utf-8').includes(sentinel); }
    catch { return true; }
  })();
  if (needsSkill) {
    fs.writeFileSync(sp, STOCK_SKILL_MD as string, 'utf-8');
    mainLogger.info('harness.bootstrap.wroteSkill', { path: sp, bytes: (STOCK_SKILL_MD as string).length });
  }

  removeLegacyToolsJson();
  removeLegacyBrowserHarness();

  ensureUserSkillsDir();
  materializeAgentSkill();
  materializeDomainSkills();
}

/**
 * Wipe and rewrite `<userData>/harness/domain-skills/` from the bundled
 * stock. Domain skills are upstream-owned reference material; full replace
 * on every launch keeps users in lockstep with whatever shipped in this
 * app version and lets us delete retired skills.
 */
function materializeDomainSkills(): void {
  materializeRawTree({
    target: domainSkillsDir(),
    prefix: DOMAIN_SKILLS_PREFIX,
    entries: Object.entries(STOCK_DOMAIN_SKILLS),
    logName: 'domainSkills',
    emptyHint: 'run `yarn sync-domain-skills` to populate stock/',
  });
}

function materializeAgentSkill(): void {
  materializeRawTree({
    target: agentSkillDir(),
    prefix: AGENT_SKILL_PREFIX,
    entries: Object.entries(STOCK_AGENT_SKILL),
    logName: 'agentSkill',
    executableBasenames: new Set(['agent-skill']),
  });
}

function ensureUserSkillsDir(): void {
  const target = userSkillsDir();
  try {
    fs.mkdirSync(target, { recursive: true });
  } catch (err) {
    mainLogger.error('harness.bootstrap.userSkills.mkdir.failed', { target, error: (err as Error).message });
    throw err;
  }
}

function removeLegacyToolsJson(): void {
  const tp = toolsPath();
  if (!fs.existsSync(tp)) return;
  try {
    fs.rmSync(tp, { force: true });
    mainLogger.info('harness.bootstrap.removedLegacyTools', { path: tp });
  } catch (err) {
    mainLogger.warn('harness.bootstrap.removeLegacyTools.failed', { path: tp, error: (err as Error).message });
  }
}

/**
 * Existing users have two generations of browser runtime materialized in
 * userData from previous launches: the vendored browser-harness-js tree, and the
 * agent-browser PATH shim that briefly replaced it. Nothing rewrites those paths
 * now, so without this the stale files would stay on disk and — worse — stay
 * reachable if an old AGENTS.md or a user-created skill still names them.
 */
function removeLegacyBrowserHarness(): void {
  const dir = harnessDir();
  const stale = [
    path.join(dir, 'helpers.js'),
    path.join(dir, 'browser-harness-js'),
    path.join(dir, 'interaction-skills'),
    path.join(dir, 'agent-browser-shim'),
    path.join(dir, 'agent-browser'),
  ];
  for (const target of stale) {
    if (!fs.existsSync(target)) continue;
    try {
      fs.rmSync(target, { recursive: true, force: true });
      mainLogger.info('harness.bootstrap.removedLegacyHarness', { path: target });
    } catch (err) {
      mainLogger.warn('harness.bootstrap.removeLegacyHarness.failed', { path: target, error: (err as Error).message });
    }
  }
}

function materializeRawTree(opts: {
  target: string;
  prefix: string;
  entries: Array<[string, string]>;
  logName: string;
  emptyHint?: string;
  executableBasenames?: Set<string>;
}): void {
  const { target, prefix, entries, logName, emptyHint, executableBasenames } = opts;
  if (entries.length === 0) {
    mainLogger.warn(`harness.bootstrap.${logName}.empty`, emptyHint ? { hint: emptyHint } : {});
    return;
  }

  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch (err) {
    mainLogger.error(`harness.bootstrap.${logName}.clear.failed`, { target, error: (err as Error).message });
    throw err;
  }

  let bytes = 0;
  for (const [modulePath, content] of entries) {
    const rel = modulePath.slice(prefix.length);
    const outPath = path.join(target, rel);
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, content, 'utf-8');
    if (executableBasenames?.has(path.basename(outPath))) fs.chmodSync(outPath, 0o755);
    bytes += content.length;
  }

  mainLogger.info(`harness.bootstrap.${logName}.wrote`, { target, files: entries.length, bytes });
}
