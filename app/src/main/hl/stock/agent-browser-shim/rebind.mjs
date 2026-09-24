#!/usr/bin/env node
/**
 * Re-resolve this session's agent-browser tab binding.
 *
 * Invoked by the `agent-browser` shim only when the daemon's pid file no longer
 * matches the cached pid — i.e. the daemon restarted. A restarted daemon loses
 * its tab binding and silently falls back to t1, which in this app is another
 * session's browser view. This script re-derives the correct tab from
 * shim-config.json and fails loudly rather than guessing.
 *
 * Runs under ELECTRON_RUN_AS_NODE=1, so only node: builtins are available —
 * the app's bundled deps are not on the module path here.
 */

import fs from 'node:fs';
import { spawnSync } from 'node:child_process';

function fail(message) {
  process.stderr.write(`agent-browser: ${message}\n`);
  process.stderr.write(
    'This session lost its browser binding and could not re-acquire it safely.\n' +
    'Do not retry blindly and do not fall back to another browser tool — report this to the user.\n',
  );
  process.exit(1);
}

const configPath = process.env.BU_AGENT_BROWSER_CONFIG;
if (!configPath) fail('BU_AGENT_BROWSER_CONFIG is not set.');

let config;
try {
  config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
} catch (err) {
  fail(`cannot read config at ${configPath}: ${err.message}`);
}

function run(args) {
  // maxBuffer must be generous: a tab's `url` can be a multi-kilobyte data:
  // document, and exceeding the 1 MB default makes spawnSync fail outright.
  const r = spawnSync(config.realBinary, args, { encoding: 'utf-8', timeout: 30_000, maxBuffer: 8 * 1024 * 1024 });
  if (r.error) return { ok: false, stdout: '', stderr: r.error.message };
  return { ok: r.status === 0, stdout: r.stdout ?? '', stderr: r.stderr ?? '' };
}

function parseTabList(stdout) {
  // Anchor on the JSON envelope, not the first `{` anywhere: percent-encoded CSS
  // inside a data: URL is full of braces and would send us to the wrong offset.
  for (const marker of ['{"success"', '{']) {
    const start = stdout.indexOf(marker);
    if (start < 0) continue;
    try {
      const tabs = JSON.parse(stdout.slice(start))?.data?.tabs;
      if (Array.isArray(tabs)) return tabs;
    } catch { /* try the next marker */ }
  }
  return null;
}

const listing = run(['tab', 'list', '--json']);
if (!listing.ok) {
  fail(`\`tab list\` failed: ${(listing.stderr || listing.stdout).trim().slice(0, 400)}`);
}
const tabs = parseTabList(listing.stdout);
if (!tabs) fail(`unparseable \`tab list --json\` output (${listing.stdout.length} bytes)`);

const pages = tabs.filter((t) => (t.type ?? 'page') === 'page');
const target = config.target ?? {};

// The marker title only survives until the agent navigates, so try it first and
// fall back to the url/title fingerprint that bind.ts keeps refreshed.
let match = pages.filter((t) => config.markerTitle && t.title === config.markerTitle);
if (match.length !== 1 && target.url) {
  match = pages.filter((t) => t.url === target.url && (target.title == null || t.title === target.title));
  if (match.length !== 1) match = pages.filter((t) => t.url === target.url);
}

if (match.length !== 1) {
  // Truncate each url: a data: URL tab can be kilobytes on its own.
  const seen = pages.map((t) => `${t.tabId}=${t.url.slice(0, 60)}`).join(', ') || 'none';
  fail(
    `expected exactly 1 tab matching url=${(target.url || '?').slice(0, 80)} title=${JSON.stringify(target.title ?? '')} ` +
    `but found ${match.length} (visible: ${seen}).`,
  );
}

const tabId = match[0].tabId;
const switched = run(['tab', tabId]);
if (!switched.ok) fail(`\`tab ${tabId}\` failed: ${(switched.stderr || switched.stdout).trim().slice(0, 300)}`);

try {
  const pid = fs.readFileSync(config.pidFile, 'utf-8').trim();
  fs.writeFileSync(config.pidCache, pid, 'utf-8');
} catch {
  // Cache write is best-effort; worst case the next call rebinds again.
}

process.stderr.write(`agent-browser: rebound session ${config.session} to ${tabId} after daemon restart\n`);
