import path from 'node:path';
import fs from 'node:fs';
import { describe, expect, it } from 'vitest';
import forgeConfig from '../../forge.config';

function ignore(file: string): boolean {
  const fn = forgeConfig.packagerConfig?.ignore;
  if (typeof fn !== 'function') throw new Error('Expected packagerConfig.ignore to be a function');
  return fn(file);
}

describe('forge packager ignore', () => {
  it('keeps Vite output for both packager-relative and absolute file paths', () => {
    const appRoot = path.resolve(__dirname, '../..');

    expect(ignore('/.vite/build/main.js')).toBe(false);
    expect(ignore(path.join(appRoot, '.vite', 'build', 'main.js'))).toBe(false);
  });

  it('ignores non-Vite sources for both packager-relative and absolute file paths', () => {
    const appRoot = path.resolve(__dirname, '../..');

    expect(ignore('/src/main/index.ts')).toBe(true);
    expect(ignore(path.join(appRoot, 'src', 'main', 'index.ts'))).toBe(true);
  });
});

describe('packaged Python agent', () => {
  it('copies only runtime Python source to the external agent resource', async () => {
    const generateAssets = forgeConfig.hooks?.generateAssets;
    if (typeof generateAssets !== 'function') throw new Error('Expected a generateAssets hook');
    await generateAssets(forgeConfig as Parameters<typeof generateAssets>[0], process.platform, process.arch);

    const staged = path.resolve(__dirname, '../../.vite/agent');
    expect(forgeConfig.packagerConfig?.extraResource).toContain(staged);
    expect(fs.readdirSync(staged).sort()).toEqual([
      'config', 'llm', 'logger', 'main.py', 'memory',
      'planner', 'protocol', 'react', 'reflection',
    ]);
    expect(fs.existsSync(path.join(staged, 'react/browser.py'))).toBe(true);
    expect(fs.existsSync(path.join(staged, 'planner/planner.py'))).toBe(true);
    expect(fs.existsSync(path.join(staged, 'reflection/reflection.py'))).toBe(true);
    expect(fs.existsSync(path.join(staged, 'memory/skills.py'))).toBe(true);
    expect(fs.existsSync(path.join(staged, 'llm/models.py'))).toBe(true);
    expect(fs.existsSync(path.join(staged, 'config/config.py'))).toBe(true);
    expect(fs.existsSync(path.join(staged, 'protocol/protocol.py'))).toBe(true);
    expect(fs.existsSync(path.join(staged, 'logger/log.py'))).toBe(true);
    const files = fs.readdirSync(staged, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile()).map((entry) => entry.name);
    expect(files.length).toBeGreaterThan(0);
    expect(files.every((file) => file.endsWith('.py'))).toBe(true);
  });
});
