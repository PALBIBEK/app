/**
 * The browser agent's calls as a person reads them. `browser_exec` runs Python against a
 * real browser and answers with what it printed and what the browser did (the page it ended
 * on, a screenshot it showed, the dialogs and errors its tabs reported); `browser_vault`
 * types a login the user saved without the agent ever seeing it.
 */

import type { ToolCallView } from '../api/types/session.types';

export const BROWSER_EXEC = 'browser_exec';
export const BROWSER_VAULT = 'browser_vault';

/** Something a tab reported during the step: a native dialog, a console error, a page error. */
export interface BrowserEventView {
  kind: 'dialog' | 'console' | 'pageerror';
  text: string;
  /** How often it happened (a page logging one error in a loop reports it once). */
  times: number;
}

/** One `browser_exec` call. */
export interface BrowserStepView {
  /** The step in plain words: the one-line comment the code starts with, else a fallback. */
  title: string;
  code: string;
  /** The page the step ended on. */
  url: string | null;
  /** The first screenshot the step showed, as a data URL. */
  screenshot: string | null;
  events: BrowserEventView[];
  /** The browser had to start afresh: tabs and logins from earlier steps are gone. */
  freshBrowser: boolean;
  timedOut: boolean;
  /** What went wrong inside the step (the code raised, the browser was unavailable). */
  error: string | null;
  /** What the code printed. */
  output: string;
  exitCode: number | null;
}

type Fields = Record<string, unknown>;

function fields(value: unknown): Fields {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Fields) : {};
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null;
}

/** The step's own name for itself: the `# comment` its code opens with. */
export function stepTitle(code: string): string | null {
  const first = code.trimStart().split('\n', 1)[0] ?? '';
  const comment = first.match(/^#+\s*(.+?)\s*$/);
  return comment ? comment[1] : null;
}

function browserEvent(raw: unknown): BrowserEventView | null {
  const e = fields(raw);
  const times = typeof e.times === 'number' && e.times > 1 ? e.times : 1;
  switch (e.kind) {
    case 'dialog': {
      const answer = e.answer === 'accept' ? 'accepted' : 'dismissed';
      const message = text(e.message);
      return { kind: 'dialog', text: `${text(e.type) ?? 'dialog'}${message ? ` “${message}”` : ''}: ${answer}`, times };
    }
    case 'console':
      return { kind: 'console', text: `console ${text(e.level) ?? 'error'}: ${text(e.text) ?? ''}`.trim(), times };
    case 'pageerror':
      return { kind: 'pageerror', text: `page error: ${text(e.text) ?? ''}`.trim(), times };
    default:
      return null;
  }
}

function firstImage(images: unknown): string | null {
  if (!Array.isArray(images)) return null;
  for (const raw of images) {
    const image = fields(raw);
    const mime = text(image.mime_type);
    const data = text(image.data);
    if (mime?.startsWith('image/') && data) return `data:${mime};base64,${data}`;
  }
  return null;
}

export function browserStep(tool: ToolCallView): BrowserStepView {
  const code = text(fields(tool.args).code) ?? '';
  const out = fields(tool.output);
  const events = Array.isArray(out.events) ? out.events.map(browserEvent).filter((e): e is BrowserEventView => e !== null) : [];
  return {
    title: stepTitle(code) ?? 'Browser step',
    code,
    url: text(out.url),
    screenshot: firstImage(out.images),
    events,
    freshBrowser: out.browser_started === true,
    timedOut: out.timed_out === true,
    error: text(out.error),
    output: text(out.output) ?? '',
    exitCode: typeof out.exit_code === 'number' ? out.exit_code : null,
  };
}

/** A URL as a person scans it: its host and path, without the scheme. */
export function shortUrl(url: string): string {
  try {
    const u = new URL(url);
    const path = u.pathname === '/' ? '' : u.pathname;
    return `${u.host}${path}${u.search}`;
  } catch {
    return url;
  }
}

/** What a `browser_vault` call did, in one line. It names the site or the item, never a value. */
export function vaultLine(tool: ToolCallView): { text: string; failed: boolean } {
  const args = fields(tool.args);
  const out = fields(tool.output);
  const handle = text(args.handle) ?? text(args.login) ?? '';
  const [kind, name] = handle.startsWith('card:') ? ['card', handle.slice(5)] : handle.startsWith('address:') ? ['address', handle.slice(8)] : ['login', handle.replace(/^login:/, '')];
  const site = shortUrl(text(out.origin) ?? text(out.login) ?? (kind === 'login' && name ? name : 'the site'));
  if (args.action === 'list') {
    if (tool.status === 'running') return { text: 'Checking what is saved…', failed: false };
    const items = Array.isArray(out.items) ? out.items.length : Array.isArray(out.logins) ? out.logins.length : 0;
    return { text: items ? `Found ${items} saved item${items === 1 ? '' : 's'}` : 'Nothing saved', failed: false };
  }
  const what = args.action === 'enter_code' ? `the code for ${site}` : kind === 'login' ? `the saved login for ${site}` : `the saved ${kind} ${name}`;
  if (tool.status === 'running') {
    return { text: args.action === 'enter_code' ? `Entering the code for ${site}…` : kind === 'login' ? `Signing in to ${site}…` : `Filling ${what}…`, failed: false };
  }
  const error = text(out.error) ?? (tool.status === 'error' ? tool.explanation ?? tool.error ?? 'the call failed' : null);
  if (error) return { text: `Could not use ${what}: ${error}`, failed: true };
  if (args.action === 'enter_code') return { text: `Entered the code for ${site}`, failed: false };
  return { text: out.submitted === true ? `Signed in to ${site}` : `Filled ${what}`, failed: false };
}
