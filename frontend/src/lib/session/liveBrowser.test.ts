import { describe, expect, it } from 'vitest';

import type { ChatMessage } from '@/lib/api/types/session.types';

import { handBackMessage, keyInput, merged, pagePoint, usedBrowser, type BrowserScreen } from './liveBrowser';

const SCREEN: BrowserScreen = { url: 'https://shop.example/verify', title: 'Verify', width: 1280, height: 720, image: '' };
const key = (k: string, mods: Partial<Record<'ctrlKey' | 'altKey' | 'metaKey' | 'shiftKey', boolean>> = {}) =>
  keyInput({ key: k, ctrlKey: false, altKey: false, metaKey: false, shiftKey: false, ...mods });

describe('the live view', () => {
  it('is offered once the agent has used a browser', () => {
    const said = (name: string): ChatMessage => ({ id: name, role: 'assistant', content: '', timestamp: '', toolCalls: [{ id: name, name, status: 'success' }] });
    expect(usedBrowser([said('bash')])).toBe(false);
    expect(usedBrowser([said('bash'), said('browser_exec')])).toBe(true);
  });

  it('turns a point on the shown image into the page’s own pixels', () => {
    expect(pagePoint({ x: 320, y: 180 }, { width: 640, height: 360 }, SCREEN)).toEqual({ x: 640, y: 360 });
    expect(pagePoint({ x: -5, y: 999 }, { width: 640, height: 360 }, SCREEN)).toEqual({ x: 0, y: 720 }, 'kept on the page');
  });

  it('sends a character as typing, and a named key with its modifiers', () => {
    expect([key('a'), key('A', { shiftKey: true }), key(' '), key('é')]).toEqual([
      { kind: 'type', text: 'a' },
      { kind: 'type', text: 'A' },
      { kind: 'type', text: ' ' },
      { kind: 'type', text: 'é' },
    ]);
    expect([key('Enter'), key('Tab', { shiftKey: true }), key('a', { ctrlKey: true }), key('F5'), key(' ', { ctrlKey: true })]).toEqual([
      { kind: 'key', key: 'Enter' },
      { kind: 'key', key: 'Shift+Tab' },
      { kind: 'key', key: 'Control+a' },
      { kind: 'key', key: 'F5' },
      { kind: 'key', key: 'Control+Space' },
    ]);
    expect([key('Shift', { shiftKey: true }), key('CapsLock'), key('Dead')]).toEqual([null, null, null]);
  });

  it('sends what was typed in one go', () => {
    expect(merged([{ kind: 'type', text: '12' }, { kind: 'type', text: '34' }, { kind: 'key', key: 'Enter' }, { kind: 'type', text: 'x' }])).toEqual([
      { kind: 'type', text: '1234' },
      { kind: 'key', key: 'Enter' },
      { kind: 'type', text: 'x' },
    ]);
  });

  it('hands back saying what the person did and where the page is', () => {
    expect(handBackMessage(SCREEN, ' solved the CAPTCHA ')).toBe(
      'I took control of the browser and did this: solved the CAPTCHA It is now on “Verify” (https://shop.example/verify). Look at the page before you go on.',
    );
    expect(handBackMessage(null, '')).toBe('I took control of the browser and I am done. Look at the page before you go on.');
  });
});
