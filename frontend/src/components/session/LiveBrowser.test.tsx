import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { sessionsAPI } from '@/lib/api/endpoints/sessions';

import { LiveBrowserDialog } from './LiveBrowser';

const SHOWN = { url: 'https://shop.example/verify', title: 'Verify', width: 1280, height: 720, image: 'AAAA' };

beforeEach(() => {
  vi.spyOn(sessionsAPI, 'browserScreen').mockResolvedValue(SHOWN);
  vi.spyOn(sessionsAPI, 'browserInput').mockImplementation(async () => ({ ...SHOWN, title: 'Verified' }));
});
afterEach(() => vi.restoreAllMocks());

function open(agentWorking = false, onHandBack = vi.fn(async () => {}), onClose = vi.fn()) {
  render(<LiveBrowserDialog sessionId="s1" agentWorking={agentWorking} onHandBack={onHandBack} onClose={onClose} />);
  return { onHandBack, onClose };
}

describe('the live view of the agent’s browser', () => {
  it('shows the tab the agent is on', async () => {
    open();
    await waitFor(() => expect(screen.getByTestId('live-screen').getAttribute('src')).toBe('data:image/jpeg;base64,AAAA'));
    expect(screen.getByTestId('live-url').textContent).toBe('Verify · shop.example/verify');
  });

  it('takes a click at the page’s own pixels, and typing in one go, once in control', async () => {
    open();
    const shot = await screen.findByTestId('live-screen');
    vi.spyOn(shot, 'getBoundingClientRect').mockReturnValue({ left: 10, top: 20, width: 640, height: 360 } as DOMRect);

    fireEvent.click(shot, { clientX: 330, clientY: 200 }); // watching: nothing is sent
    expect(sessionsAPI.browserInput).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Take control/ }));
    fireEvent.click(shot, { clientX: 330, clientY: 200 });
    const page = screen.getByTestId('live-page');
    for (const k of ['1', '2', '3']) fireEvent.keyDown(page, { key: k });
    fireEvent.keyDown(page, { key: 'Enter' });

    await waitFor(() => expect(vi.mocked(sessionsAPI.browserInput).mock.calls.map((c) => c[1])).toEqual([
      { kind: 'click', x: 640, y: 360 },
      { kind: 'type', text: '123' },
      { kind: 'key', key: 'Enter' },
    ]));
    await waitFor(() => expect(screen.getByTestId('live-url').textContent).toContain('Verified'));
  });

  it('is only watched while the agent works', async () => {
    open(true);
    await screen.findByTestId('live-screen');

    expect(screen.getByTestId('live-watching').textContent).toContain('you can watch');
    expect(screen.queryByRole('button', { name: /Take control/ })).toBeNull();
  });

  it('hands back telling the agent what the person did', async () => {
    const { onHandBack, onClose } = open();
    await screen.findByTestId('live-screen');
    fireEvent.click(screen.getByRole('button', { name: /Take control/ }));
    fireEvent.change(screen.getByLabelText('What you did, for the agent'), { target: { value: 'solved the CAPTCHA' } });

    await act(async () => fireEvent.click(screen.getByRole('button', { name: /Hand back/ })));

    expect(onHandBack).toHaveBeenCalledWith(expect.stringContaining('did this: solved the CAPTCHA It is now on “Verify”'));
    expect(onClose).toHaveBeenCalled();
  });

  it('says why when the browser cannot be shown', async () => {
    vi.mocked(sessionsAPI.browserScreen).mockRejectedValue(new Error('no browser is open: the agent has not used one in this sandbox'));
    open();
    expect(await screen.findByText(/no browser is open/)).toBeTruthy();
  });
});
