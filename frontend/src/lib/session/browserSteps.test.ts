import { describe, expect, it } from 'vitest';

import type { ToolCallView } from '@/lib/api/types/session.types';

import { browserStep, shortUrl, stepTitle, vaultLine } from './browserSteps';

const call = (over: Partial<ToolCallView>): ToolCallView => ({ id: 'c1', name: 'browser_exec', status: 'success', ...over });

describe('a browser step', () => {
  it('is named by the comment its code opens with', () => {
    expect(stepTitle('# Searching the catalogue for paper towels\npage.goto(url)')).toBe('Searching the catalogue for paper towels');
    expect(stepTitle('\n  ## Opening the basket  \nx = 1')).toBe('Opening the basket');
    expect(stepTitle('page.goto(url)  # not a title')).toBeNull();
    expect(browserStep(call({ args: { code: 'print(1)' } })).title).toBe('Browser step');
  });

  it('shows the page it ended on, the screenshot it showed and what the browser reported', () => {
    const step = browserStep(
      call({
        args: { code: '# Checking out\nact(("click", "0-4"))' },
        output: {
          exit_code: 0,
          output: 'ok',
          url: 'https://shop.example.com/basket?step=2',
          images: [{ mime_type: 'image/jpeg', data: 'AAAA' }],
          events: [
            { kind: 'dialog', tab: 't1', type: 'confirm', message: 'Leave the page?', answer: 'accept' },
            { kind: 'console', tab: 't1', level: 'error', text: 'Failed to load', times: 3 },
            { kind: 'pageerror', tab: 't1', text: 'TypeError: x is undefined' },
            { kind: 'navigation', tab: 't1' },
          ],
          browser_started: true,
        },
      }),
    );

    expect(step.url).toBe('https://shop.example.com/basket?step=2');
    expect(step.screenshot).toBe('data:image/jpeg;base64,AAAA');
    expect(step.events).toEqual([
      { kind: 'dialog', text: 'confirm “Leave the page?”: accepted', times: 1 },
      { kind: 'console', text: 'console error: Failed to load', times: 3 },
      { kind: 'pageerror', text: 'page error: TypeError: x is undefined', times: 1 },
    ]);
    expect(step.freshBrowser).toBe(true);
    expect([step.output, step.exitCode, step.error, step.timedOut]).toEqual(['ok', 0, null, false]);
  });

  it('takes nothing for an image that is not one', () => {
    const step = browserStep(call({ output: { images: [{ mime_type: 'text/html', data: 'PGI+' }, 'x'] } }));
    expect(step.screenshot).toBeNull();
  });

  it('copes with a call that has no result yet', () => {
    const step = browserStep(call({ status: 'running', args: { code: '# Logging in' } }));
    expect(step).toMatchObject({ title: 'Logging in', url: null, screenshot: null, events: [], output: '', exitCode: null });
  });

  it('reads a URL without its scheme', () => {
    expect(shortUrl('https://shop.example.com/')).toBe('shop.example.com');
    expect(shortUrl('https://a.example/b?c=1')).toBe('a.example/b?c=1');
    expect(shortUrl('not a url')).toBe('not a url');
  });
});

describe('a saved login', () => {
  const vault = (over: Partial<ToolCallView>): ToolCallView => call({ name: 'browser_vault', ...over });

  it('names the site it signed in to, never the login', () => {
    const tool = vault({
      args: { action: 'fill', login: 'https://shop.example.com', username: '0-3', password: '0-4', submit: '0-5' },
      output: { login: 'https://shop.example.com', filled: ['username', 'password'], submitted: true, url: 'https://shop.example.com/account' },
    });
    expect(vaultLine(tool)).toEqual({ text: 'Signed in to shop.example.com', failed: false });
    expect(vaultLine({ ...tool, output: { ...(tool.output as object), submitted: false } }).text).toBe('Filled the saved login for shop.example.com');
  });

  it('says why a login could not be used', () => {
    const tool = vault({ args: { action: 'fill', login: 'https://a.example' }, output: { error: 'no saved login for https://a.example', logins: [] } });
    expect(vaultLine(tool)).toEqual({ text: 'Could not use the saved login for a.example: no saved login for https://a.example', failed: true });
    expect(vaultLine(vault({ status: 'error', args: { action: 'fill', login: 'https://a.example' }, explanation: 'Denied.' })).text).toContain('Denied.');
  });

  it('counts the sites a list found', () => {
    expect(vaultLine(vault({ args: { action: 'list' }, output: { logins: ['https://a.example', 'https://b.example'] } })).text).toBe('Found saved logins for 2 sites');
    expect(vaultLine(vault({ args: { action: 'list' }, output: { logins: [] } })).text).toBe('No saved logins');
    expect(vaultLine(vault({ status: 'running', args: { action: 'fill', login: 'https://a.example' } })).text).toBe('Signing in to a.example…');
  });
});
