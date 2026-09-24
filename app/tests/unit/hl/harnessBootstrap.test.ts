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
  agentBrowserShimDir,
  agentSkillDir,
  bootstrapHarness,
  harnessDir,
  skillIdToPath,
  skillPath,
  skillPathFromMeta,
  toolsPath,
  userSkillsDir,
} = await import('../../../src/main/hl/harness');

describe('bootstrapHarness agent-browser materialization', () => {
  beforeEach(() => {
    fs.rmSync(path.join(mockState.userData, 'harness'), { recursive: true, force: true });
  });

  afterAll(() => {
    fs.rmSync(mockState.userData, { recursive: true, force: true });
  });

  test('writes the agent-browser shim and removes legacy TOOLS.json', () => {
    fs.mkdirSync(path.dirname(toolsPath()), { recursive: true });
    fs.writeFileSync(toolsPath(), '{}\n');

    bootstrapHarness();

    const shim = path.join(agentBrowserShimDir(), 'agent-browser');
    const shimCmd = path.join(agentBrowserShimDir(), 'agent-browser.cmd');
    const rebind = path.join(agentBrowserShimDir(), 'rebind.mjs');
    const agentSkill = path.join(agentSkillDir(), 'agent-skill');
    const userSkill = path.join(userSkillsDir(), 'general', 'existing', 'SKILL.md');
    fs.mkdirSync(path.dirname(userSkill), { recursive: true });
    fs.writeFileSync(userSkill, '# Existing\n');

    bootstrapHarness();

    expect(fs.readFileSync(skillPath(), 'utf-8')).toContain('agent-browser');
    expect(fs.existsSync(toolsPath())).toBe(false);
    expect(fs.existsSync(shim)).toBe(true);
    expect(fs.existsSync(rebind)).toBe(true);
    expect(fs.existsSync(agentSkill)).toBe(true);
    expect(fs.existsSync(path.join(agentSkillDir(), 'agent-skill.cmd'))).toBe(true);
    expect(fs.existsSync(userSkill)).toBe(true);
    // Windows launcher ships alongside the POSIX shim so engine CLIs can find
    // it via PATHEXT (.CMD) instead of hitting the no-handler popup on the
    // extensionless shell script.
    expect(fs.existsSync(shimCmd)).toBe(true);
    expect(fs.readFileSync(shimCmd, 'utf-8')).toContain('BU_AGENT_BROWSER_SHIMCMD');
    // Executable-bit assert: skipped on Windows because NTFS permission
    // mapping doesn't expose POSIX exec bits the way the test asserts.
    if (process.platform !== 'win32') {
      expect(fs.statSync(shim).mode & 0o111).not.toBe(0);
      expect(fs.statSync(agentSkill).mode & 0o111).not.toBe(0);
    }
  });

  test('clears the browser-harness-js runtime left behind by older versions', () => {
    const dir = harnessDir();
    const legacyCli = path.join(dir, 'browser-harness-js', 'sdk', 'browser-harness-js');
    fs.mkdirSync(path.dirname(legacyCli), { recursive: true });
    fs.writeFileSync(legacyCli, '#!/bin/sh\n');
    fs.writeFileSync(path.join(dir, 'helpers.js'), 'module.exports = {};\n');
    fs.mkdirSync(path.join(dir, 'interaction-skills'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'interaction-skills', 'screenshots.md'), '# legacy\n');

    bootstrapHarness();

    expect(fs.existsSync(path.join(dir, 'browser-harness-js'))).toBe(false);
    expect(fs.existsSync(path.join(dir, 'helpers.js'))).toBe(false);
    expect(fs.existsSync(path.join(dir, 'interaction-skills'))).toBe(false);
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
