/**
 * The agent's browser, live: what a person sees (a JPEG of its current tab, refreshed) and
 * what their hand on it sends (a click, typing, a key, a scroll), in the page's CSS pixels.
 * The supervisor takes input only while the agent is not working; a person hands back with
 * a message saying what they did, so the agent knows the page changed.
 */

import type { ChatMessage } from '../api/types/session.types';
import { BROWSER_EXEC } from './browserSteps';

export interface BrowserScreen {
  url: string;
  title: string;
  width: number;
  height: number;
  /** A base64 JPEG of the tab's viewport, `width` by `height` CSS pixels. */
  image: string;
}

export type BrowserInput =
  | { kind: 'click'; x: number; y: number }
  | { kind: 'type'; text: string }
  | { kind: 'key'; key: string }
  | { kind: 'scroll'; x: number; y: number; dx: number; dy: number };

/** Whether the conversation's agent has used a browser (only then is there one to watch). */
export function usedBrowser(messages: readonly ChatMessage[]): boolean {
  return messages.some((m) => m.toolCalls?.some((t) => t.name === BROWSER_EXEC));
}

/** A point on the shown image (`at`, in its displayed pixels, `shown` its displayed size) as
 * the page's own CSS pixels. */
export function pagePoint(at: { x: number; y: number }, shown: { width: number; height: number }, screen: BrowserScreen): { x: number; y: number } {
  const scale = (n: number, of: number, to: number): number => Math.round(Math.min(Math.max(n, 0), of) * (to / Math.max(of, 1)));
  return { x: scale(at.x, shown.width, screen.width), y: scale(at.y, shown.height, screen.height) };
}

const NAMED: Record<string, string> = {
  ' ': 'Space',
  Enter: 'Enter',
  Tab: 'Tab',
  Escape: 'Escape',
  Backspace: 'Backspace',
  Delete: 'Delete',
  Home: 'Home',
  End: 'End',
  PageUp: 'PageUp',
  PageDown: 'PageDown',
  ArrowUp: 'ArrowUp',
  ArrowDown: 'ArrowDown',
  ArrowLeft: 'ArrowLeft',
  ArrowRight: 'ArrowRight',
};

/** A key pressed on the live view as what it sends: a character typed, a named key (with its
 * modifiers, in Playwright's names), or nothing (a modifier alone, a key it does not take). */
export function keyInput(e: { key: string; ctrlKey: boolean; altKey: boolean; metaKey: boolean; shiftKey: boolean }): BrowserInput | null {
  const chord = e.ctrlKey || e.altKey || e.metaKey;
  if (e.key.length === 1 && !chord) return { kind: 'type', text: e.key };
  const named = NAMED[e.key] ?? (/^F\d{1,2}$/.test(e.key) ? e.key : e.key.length === 1 && /[A-Za-z0-9]/.test(e.key) ? e.key.toLowerCase() : null);
  if (!named) return null;
  const mods = [e.ctrlKey && 'Control', e.altKey && 'Alt', e.metaKey && 'Meta', e.shiftKey && (named.length > 1 || chord) && 'Shift'].filter(Boolean);
  return { kind: 'key', key: [...mods, named].join('+') };
}

/** Consecutive typing, as one input: the live view sends what was typed in one go. */
export function merged(queue: readonly BrowserInput[]): BrowserInput[] {
  const out: BrowserInput[] = [];
  for (const input of queue) {
    const last = out[out.length - 1];
    if (input.kind === 'type' && last?.kind === 'type' && last.text.length + input.text.length <= 1000) {
      out[out.length - 1] = { kind: 'type', text: last.text + input.text };
    } else {
      out.push(input);
    }
  }
  return out;
}

/** What the agent is told when the person hands the browser back. */
export function handBackMessage(screen: BrowserScreen | null, note: string): string {
  const done = note.trim() ? `I took control of the browser and did this: ${note.trim()}` : 'I took control of the browser and I am done.';
  return screen ? `${done} It is now on “${screen.title}” (${screen.url}). Look at the page before you go on.` : `${done} Look at the page before you go on.`;
}
