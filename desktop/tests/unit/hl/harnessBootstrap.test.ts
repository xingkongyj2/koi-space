import fs from 'node:fs';
import path from 'node:path';
import { afterAll, beforeEach, describe, expect, test, vi } from 'vitest';

const mockState = vi.hoisted(() => {
  const fsMod = require('node:fs') as typeof import('node:fs');
  const osMod = require('node:os') as typeof import('node:os');
  const pathMod = require('node:path') as typeof import('node:path');
  return {
    userData: fsMod.mkdtempSync(pathMod.join(osMod.tmpdir(), 'bu-harness-bootstrap-')),
  };
});

vi.mock('electron', () => ({
  app: {
    getPath: vi.fn(() => mockState.userData),
  },
}));

const {
  agentSkillDir,
  bootstrapHarness,
  domainSkillsDir,
  harnessDir,
  skillIdToPath,
  skillPath,
  skillPathFromMeta,
  toolsPath,
  userSkillsDir,
} = await import('../../../src/main/hl/harness');

describe('bootstrapHarness', () => {
  beforeEach(() => {
    fs.rmSync(path.join(mockState.userData, 'harness'), { recursive: true, force: true });
  });

  afterAll(() => {
    fs.rmSync(mockState.userData, { recursive: true, force: true });
  });

  test('writes AGENTS.md and the agent-skill CLI, and removes legacy TOOLS.json', () => {
    fs.mkdirSync(path.dirname(toolsPath()), { recursive: true });
    fs.writeFileSync(toolsPath(), '{}\n');

    bootstrapHarness();

    const agentSkill = path.join(agentSkillDir(), 'agent-skill');
    const userSkill = path.join(userSkillsDir(), 'general', 'existing', 'SKILL.md');
    fs.mkdirSync(path.dirname(userSkill), { recursive: true });
    fs.writeFileSync(userSkill, '# Existing\n');

    bootstrapHarness();

    expect(fs.readFileSync(skillPath(), 'utf-8')).toContain('agent-skill search');
    expect(fs.existsSync(toolsPath())).toBe(false);
    expect(fs.existsSync(agentSkill)).toBe(true);
    // Windows launcher ships alongside the POSIX script so an engine can find it
    // via PATHEXT (.CMD) instead of hitting the no-handler popup on the
    // extensionless shell script.
    expect(fs.existsSync(path.join(agentSkillDir(), 'agent-skill.cmd'))).toBe(true);
    // Domain skills are synced from upstream, so assert the tree landed rather
    // than any one file that a future sync could rename.
    expect(fs.existsSync(path.join(domainSkillsDir(), 'VERSION'))).toBe(true);
    expect(fs.readdirSync(domainSkillsDir()).length).toBeGreaterThan(10);
    expect(fs.existsSync(userSkill)).toBe(true);
    // Executable-bit assert: skipped on Windows because NTFS permission
    // mapping doesn't expose POSIX exec bits the way the test asserts.
    if (process.platform !== 'win32') {
      expect(fs.statSync(agentSkill).mode & 0o111).not.toBe(0);
    }
  });

  test('materializes no browser tooling — the Python agent owns agent-browser', () => {
    bootstrapHarness();
    const dir = harnessDir();
    expect(fs.existsSync(path.join(dir, 'agent-browser-shim'))).toBe(false);
    expect(fs.existsSync(path.join(dir, 'browser-harness-js'))).toBe(false);
  });

  test('clears every browser runtime left behind by older versions', () => {
    const dir = harnessDir();
    for (const file of [
      path.join(dir, 'browser-harness-js', 'sdk', 'browser-harness-js'),
      path.join(dir, 'helpers.js'),
      path.join(dir, 'interaction-skills', 'screenshots.md'),
      path.join(dir, 'agent-browser-shim', 'agent-browser'),
      path.join(dir, 'agent-browser', 'sockets', 'bu-stale.pid'),
    ]) {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, '#!/bin/sh\n');
    }

    bootstrapHarness();

    for (const stale of ['browser-harness-js', 'helpers.js', 'interaction-skills', 'agent-browser-shim', 'agent-browser']) {
      expect(fs.existsSync(path.join(dir, stale)), `${stale} should have been removed`).toBe(false);
    }
  });

  test('rejects traversal and absolute skill IDs before converting to paths', () => {
    const root = path.join(mockState.userData, 'harness');

    expect(skillIdToPath('domain/github/repo', root)).toBe(path.join(root, 'domain-skills', 'github/repo.md'));
    expect(skillIdToPath("'domain/github/repo'", root)).toBe(path.join(root, 'domain-skills', 'github/repo.md'));
    expect(skillIdToPath('domain/../secret', root)).toBeNull();
    expect(skillIdToPath('domain/./github', root)).toBeNull();
    expect(skillIdToPath('domain//github', root)).toBeNull();
    expect(skillIdToPath('/domain/github', root)).toBeNull();
    expect(skillIdToPath('domain/C:/secret', root)).toBeNull();
    expect(skillPathFromMeta({ domain: 'user', topic: '../secret' }, root)).toBeNull();
    expect(skillPathFromMeta({ domain: 'domain', topic: '/github/repo' }, root)).toBeNull();
  });
});
