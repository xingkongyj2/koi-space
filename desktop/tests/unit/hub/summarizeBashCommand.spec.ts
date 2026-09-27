import { describe, expect, it } from 'vitest';
import { getToolBashCommand, getToolLabel, parseBashResult, summarizeBashCommand } from '../../../src/renderer/hub/chat/toolLabels';

describe('summarizeBashCommand', () => {
  it('returns null for unknown commands', () => {
    expect(summarizeBashCommand('weirdtool --do-stuff')).toBeNull();
  });

  it('unwraps /bin/zsh -lc "..." before matching', () => {
    expect(summarizeBashCommand(`/bin/zsh -lc "sed -n '1,260p' AGENTS.md"`)).toEqual({
      active: 'Reading', completed: 'Read', value: 'AGENTS.md',
    });
  });

  it('summarizes cat as Read FILE', () => {
    expect(summarizeBashCommand('cat package.json')).toEqual({
      active: 'Reading', completed: 'Read', value: 'package.json',
    });
  });

  it('summarizes ls as Looked at files (folded into label)', () => {
    expect(summarizeBashCommand('ls -la')).toEqual({
      active: 'Looking at files', completed: 'Looked at files', value: '',
    });
  });

  it('summarizes find as Looked for files (folded into label)', () => {
    expect(summarizeBashCommand('find ./src -name "*.ts"')).toEqual({
      active: 'Looking for files', completed: 'Looked for files', value: '',
    });
  });

  it('summarizes grep with quoted pattern', () => {
    expect(summarizeBashCommand(`grep -r "useState" src/`)).toEqual({
      active: 'Searching for', completed: 'Searched for', value: 'useState',
    });
  });

  it('summarizes git status/diff/log/show as Reviewed recent changes', () => {
    expect(summarizeBashCommand('git status --short')?.completed).toBe('Reviewed recent changes');
    expect(summarizeBashCommand('git diff')?.completed).toBe('Reviewed recent changes');
    expect(summarizeBashCommand('git log')?.completed).toBe('Reviewed recent changes');
  });

  it('summarizes git commit as Saved progress', () => {
    expect(summarizeBashCommand('git commit -m "hi"')).toEqual({
      active: 'Saving progress', completed: 'Saved progress', value: '',
    });
  });

  it('summarizes git push as Sent changes to the cloud', () => {
    expect(summarizeBashCommand('git push origin main')?.completed).toBe('Sent changes to the cloud');
  });

  it('summarizes git checkout BRANCH', () => {
    expect(summarizeBashCommand('git checkout feature/chat-view')).toEqual({
      active: 'Switching to', completed: 'Switched to', value: 'feature/chat-view',
    });
  });

  it('summarizes curl as Visited URL', () => {
    expect(summarizeBashCommand('curl https://example.com')).toEqual({
      active: 'Visiting', completed: 'Visited', value: 'https://example.com',
    });
  });

  it('summarizes npm install as Installed tools', () => {
    expect(summarizeBashCommand('npm install lodash')?.completed).toBe('Installed tools');
  });

  it('summarizes npm test as Ran tests', () => {
    expect(summarizeBashCommand('yarn test')?.completed).toBe('Ran tests');
  });

  it('summarizes npm run build as Built project', () => {
    expect(summarizeBashCommand('npm run build')?.completed).toBe('Built project');
  });

  it('strips dirname from basename targets', () => {
    expect(summarizeBashCommand('cat /a/b/c.md')?.value).toBe('c.md');
  });

  it('labels agent-browser open with the URL as the specific value', () => {
    expect(summarizeBashCommand('agent-browser open https://linkedin.com/mynetwork')).toEqual({
      active: 'Visiting', completed: 'Visited', value: 'https://linkedin.com/mynetwork',
    });
  });

  it('labels the goto alias like open', () => {
    expect(summarizeBashCommand('agent-browser goto https://x.com/home')?.completed).toBe('Visited');
  });

  it('labels click with the element ref as value', () => {
    expect(summarizeBashCommand('agent-browser click @e5')).toEqual({
      active: 'Clicking', completed: 'Clicked', value: '@e5',
    });
  });

  it('labels snapshot as reading the page, ignoring its flags', () => {
    expect(summarizeBashCommand('agent-browser snapshot -i')?.completed).toBe('Read page');
  });

  it('labels screenshot', () => {
    expect(summarizeBashCommand('agent-browser screenshot /tmp/shot.png')?.completed).toBe('Took screenshot');
  });

  it('labels get text with the field as value', () => {
    expect(summarizeBashCommand('agent-browser get text @e3')).toEqual({
      active: 'Reading page', completed: 'Read page', value: 'text',
    });
  });

  it('labels find with the locator kind and value', () => {
    expect(summarizeBashCommand('agent-browser find role button click --name Submit')).toEqual({
      active: 'Looking for', completed: 'Looked for', value: 'role button',
    });
  });

  it('skips global flags when locating the verb', () => {
    expect(summarizeBashCommand('agent-browser --json open https://example.com')?.value).toBe('https://example.com');
  });

  it('labels the first browser command in an && chain', () => {
    const cmd = 'agent-browser open https://example.com && agent-browser snapshot -i';
    expect(summarizeBashCommand(cmd)).toEqual({
      active: 'Visiting', completed: 'Visited', value: 'https://example.com',
    });
  });

  it('labels agent-browser inside a shell wrapper', () => {
    expect(summarizeBashCommand(`/bin/zsh -lc "agent-browser open https://x.com"`)?.value).toBe('https://x.com');
  });

  it('labels an absolute path to the shim like a bare invocation', () => {
    expect(summarizeBashCommand('/Users/me/harness/agent-browser-shim/agent-browser reload')?.completed)
      .toBe('Reloaded page');
  });

  it('falls back to a generic browser label for an unrecognised verb', () => {
    expect(summarizeBashCommand('agent-browser vitals https://x.com')).toEqual({
      active: 'Using browser', completed: 'Used browser', value: 'vitals',
    });
  });

  it('maps python -c to Ran Python code', () => {
    expect(summarizeBashCommand(`python3 -c "print('hi')"`)?.completed).toBe('Ran Python code');
  });

  it('maps node -e to Ran JavaScript code', () => {
    expect(summarizeBashCommand(`node -e "console.log(1)"`)?.completed).toBe('Ran JavaScript code');
  });

  it('falls back to binary name for unknown heredoc', () => {
    expect(summarizeBashCommand(`weird-tool <<EOF\nfoo\nEOF`)).toEqual({
      active: 'Running', completed: 'Ran', value: 'weird-tool',
    });
  });

  it('summarizes head -n N FILE', () => {
    expect(summarizeBashCommand('head -n 50 src/index.ts')?.value).toBe('index.ts');
  });

  it('summarizes mkdir as Created folder', () => {
    expect(summarizeBashCommand('mkdir -p build/out')).toEqual({
      active: 'Creating folder', completed: 'Created folder', value: 'out',
    });
  });

  it('summarizes rm as Deleted', () => {
    expect(summarizeBashCommand('rm -rf node_modules')?.completed).toBe('Deleted');
  });

  it('summarizes echo > FILE as Saved to', () => {
    expect(summarizeBashCommand('echo "hello" > out.txt')).toEqual({
      active: 'Saving to', completed: 'Saved to', value: 'out.txt',
    });
  });

  it('summarizes pwd as Checked current folder', () => {
    expect(summarizeBashCommand('pwd')?.completed).toBe('Checked current folder');
  });

  it('summarizes standalone cd as Changed folder', () => {
    expect(summarizeBashCommand('cd /tmp/foo')).toEqual({
      active: 'Changing folder to', completed: 'Changed folder to', value: 'foo',
    });
  });

  it('strips leading "cd X &&" so the chained command gets labeled', () => {
    expect(summarizeBashCommand('cd /tmp/foo && git status')?.completed)
      .toBe('Reviewed recent changes');
  });

  it('strips leading "cd X &&" before an agent-browser call', () => {
    const cmd = `/bin/zsh -lc "cd /work && agent-browser press Enter"`;
    expect(summarizeBashCommand(cmd)?.completed).toBe('Pressed key');
  });
});

describe('parseBashResult', () => {
  it('recovers failed status metadata from truncated JSON', () => {
    const raw = '{"stdout":"","stderr":"boom","exit_code":2,"status":"failed","duration_ms":123';
    expect(parseBashResult(raw)).toEqual({
      output: 'boom',
      isError: true,
      durationMs: 123,
    });
  });
});

describe('getToolBashCommand', () => {
  it('returns the full structured bash command without display truncation', () => {
    const command = `printf '${'x'.repeat(120)}'`;
    expect(getToolBashCommand('bash', JSON.stringify({ command }))).toBe(command);
  });
});

describe('getToolLabel', () => {
  it('uses canonical labels for aliases without explicit label entries', () => {
    expect(getToolLabel('browser_evaluate', 'completed')).toBe('Ran JavaScript');
    expect(getToolLabel('browser_select_dropdown', 'running')).toBe('Selecting option');
  });
});
