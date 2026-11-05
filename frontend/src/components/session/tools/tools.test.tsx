import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useMemo } from 'react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { ChatMessage, ToolCallView } from '@/lib/api/types/session.types';
import type { ReportAction } from '@/lib/session/useReports';

import { useToolRenderer } from '.';
import { MessageBubble } from '../parts';
import { Markdown } from './Markdown';

beforeAll(() => {
  // jsdom has constructable stylesheets but not replaceSync, which the basic catalog's
  // Web Components use for their theme.
  if (!('replaceSync' in CSSStyleSheet.prototype)) Object.assign(CSSStyleSheet.prototype, { replaceSync() {} });
});

const CATALOG = 'https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json';
const V = 'v0.9';

const NO_SUBAGENTS = new Map<string, string>();

/** A conversation of one assistant message making `tools`, then `after` (a person's answer, say). */
function Conversation({
  tools,
  after = [],
  sendAction,
}: {
  tools: ToolCallView[];
  after?: ChatMessage[];
  sendAction?: (action: ReportAction) => Promise<void>;
}): JSX.Element {
  const messages = useMemo<ChatMessage[]>(
    () => [{ id: 'm1', role: 'assistant', content: '', timestamp: '2026-11-05T10:00:00Z', toolCalls: tools }, ...after],
    [tools, after],
  );
  const renderTool = useToolRenderer({ sessionId: 's1', subagentOf: NO_SUBAGENTS, messages, sendAction });
  return <div>{tools.map(renderTool)}</div>;
}

const report = (id: string, messages: unknown[]): ToolCallView => ({ id, name: 'show_ui', status: 'success', args: { messages } });

const PRICES = [
  { version: V, createSurface: { surfaceId: 'prices', catalogId: CATALOG } },
  {
    version: V,
    updateComponents: {
      surfaceId: 'prices',
      components: [
        { id: 'root', component: 'Column', children: ['title', 'body'] },
        { id: 'title', component: 'Text', text: 'Paper towels', variant: 'h2' },
        { id: 'body', component: 'Text', text: { path: '/cheapest' } },
      ],
    },
  },
  { version: V, updateDataModel: { surfaceId: 'prices', path: '/', value: { cheapest: 'Cheapest: **£3**, at A' } } },
];

describe('the browser agent’s calls', () => {
  it('draw a step as what it was for, with the page, the screenshot and what the browser reported', () => {
    const step: ToolCallView = {
      id: 'b1',
      name: 'browser_exec',
      status: 'success',
      args: { code: '# Searching for paper towels\nprint(snapshot())' },
      output: {
        exit_code: 0,
        output: '[0-1] search box',
        url: 'https://shop.example.com/search?q=towels',
        images: [{ mime_type: 'image/jpeg', data: 'AAAA' }],
        events: [{ kind: 'dialog', type: 'alert', message: 'Cookies?', answer: 'dismiss' }],
        browser_started: true,
      },
    };
    render(<Conversation tools={[step]} />);

    expect(screen.getByText('Searching for paper towels')).toBeTruthy();
    expect(screen.getByTestId('browser-url').textContent).toBe('shop.example.com/search?q=towels');
    expect(screen.getByTestId('browser-screenshot').getAttribute('src')).toBe('data:image/jpeg;base64,AAAA');
    expect(screen.getByTestId('browser-event').textContent).toBe('alert “Cookies?”: dismissed');
    expect(screen.getByTestId('browser-fresh')).toBeTruthy();
    expect(screen.queryByTestId('tool-output')).toBeNull(); // the code and raw output fold away
    fireEvent.click(screen.getByRole('button', { name: /Expand the browser step/ }));
    expect(screen.getByTestId('tool-output').textContent).toBe('[0-1] search box');
  });

  it('say why a step failed', () => {
    const step: ToolCallView = { id: 'b2', name: 'browser_exec', status: 'success', args: { code: '# Paying' }, output: { exit_code: 1, error: 'TimeoutError: no #pay button' } };
    render(<Conversation tools={[step]} />);
    expect(screen.getByTestId('tool-explanation').textContent).toContain('no #pay button');
    expect(screen.getByText('Failed')).toBeTruthy();
  });

  it('draw a saved login as the site, never the login', () => {
    const fill: ToolCallView = {
      id: 'v1',
      name: 'browser_vault',
      status: 'success',
      args: { action: 'fill', login: 'https://shop.example.com', username: '#user', password: '#pass', submit: '#go' },
      output: { login: 'https://shop.example.com', filled: ['username', 'password'], submitted: true },
    };
    render(<Conversation tools={[fill]} />);
    expect(screen.getByTestId('vault-card').textContent).toContain('Signed in to shop.example.com');
    expect(screen.getByTestId('vault-card').textContent).not.toContain('#pass');
  });

  it('leave every other tool to the generic card', () => {
    render(<Conversation tools={[{ id: 't1', name: 'bash', status: 'success', args: { command: 'ls' }, output: 'a.txt' }]} />);
    expect(screen.getByTestId('tool-card')).toBeTruthy();
  });
});

describe('a report', () => {
  it('is drawn by A2UI where the call that created it sits, in its latest state', async () => {
    const later = report('r2', [{ version: V, updateDataModel: { surfaceId: 'prices', path: '/cheapest', value: 'Cheapest: **£2**, at B' } }]);
    const { rerender } = render(<Conversation tools={[report('r1', PRICES)]} />);

    await waitFor(() => expect(screen.getByTestId('report-surface').textContent).toContain('Paper towels'));
    expect(screen.getByText('£3').tagName).toBe('STRONG'); // its Markdown, drawn as elements

    rerender(<Conversation tools={[report('r1', PRICES), later]} />);
    await waitFor(() => expect(screen.getByText('£2')).toBeTruthy());
    expect(screen.getAllByTestId('report-surface')).toHaveLength(1);
    expect(screen.getByTestId('report-line').textContent).toBe('Updated the report above.');
  });

  it('says when it was removed later', async () => {
    const removed = report('r2', [{ version: V, deleteSurface: { surfaceId: 'prices' } }]);
    render(<Conversation tools={[report('r1', PRICES), removed]} />);
    await waitFor(() => expect(screen.getAllByTestId('report-line').map((l) => l.textContent)).toEqual([
      'This report was removed later in the conversation.',
      'Removed a report from the conversation.',
    ]));
    expect(screen.queryByTestId('report-surface')).toBeNull();
  });

  it('prints alone, as it is drawn now, to save as a PDF', async () => {
    let printed: string | null = null;
    const print = vi.spyOn(window, 'print').mockImplementation(() => {
      printed = document.querySelector('[data-print-target]')?.textContent ?? null;
    });
    render(<Conversation tools={[report('r1', PRICES)]} />);
    await waitFor(() => expect(screen.getByTestId('report-surface').textContent).toContain('Paper towels'));

    fireEvent.click(screen.getByRole('button', { name: 'Print the report or save it as a PDF' }));
    window.dispatchEvent(new Event('afterprint'));

    expect(print).toHaveBeenCalledTimes(1);
    expect(printed).toContain('Paper towels');
    expect(screen.getByRole('button', { name: 'Save the report as a PNG' })).toBeTruthy();
    print.mockRestore();
  });

  it('shows the A2UI it was drawn from, on request', async () => {
    render(<Conversation tools={[report('r1', PRICES)]} />);
    fireEvent.click(screen.getByRole('button', { name: /Show A2UI/ }));
    expect(JSON.parse(screen.getByTestId('report-a2ui').textContent ?? '')).toEqual(PRICES);
  });

  it('reads as a refused call when it was refused', () => {
    const refused: ToolCallView = { ...report('r1', [{ version: V }]), status: 'error', error: 'the messages are not A2UI the chat can draw' };
    render(<Conversation tools={[refused]} />);
    expect(screen.getByTestId('tool-explanation').textContent).toContain('not A2UI the chat can draw');
  });
});

const ASK = [
  { version: V, createSurface: { surfaceId: 'which', catalogId: CATALOG } },
  {
    version: V,
    updateComponents: {
      surfaceId: 'which',
      components: [
        { id: 'root', component: 'Column', children: ['pick', 'go'] },
        { id: 'pick', component: 'ChoicePicker', options: [{ label: 'Shop A', value: 'a' }, { label: 'Shop B', value: 'b' }], value: { path: '/pick' } },
        { id: 'go', component: 'Button', child: 'go_label', action: { event: { name: 'chosen', context: { pick: { path: '/pick' } } } } },
        { id: 'go_label', component: 'Text', text: 'Add to basket' },
      ],
    },
  },
  { version: V, updateDataModel: { surfaceId: 'which', path: '/', value: { pick: ['a'] } } },
];

const answer = (pick: string): ChatMessage => ({
  id: 'u2',
  role: 'user',
  content: `UI action: ${JSON.stringify({ context: { pick: [pick] }, name: 'chosen', sourceComponentId: 'go', surfaceId: 'which' })}`,
  timestamp: '2026-11-05T10:01:00Z',
});

async function clickAdd(container: HTMLElement): Promise<void> {
  await waitFor(() => expect(container.querySelector('a2ui-basic-button button')).toBeTruthy());
  fireEvent.click(container.querySelector('a2ui-basic-button button')!);
}

describe('a report that asks', () => {
  it('sends the person’s choice once, and says it is on its way', async () => {
    const send = vi.fn(() => new Promise<void>(() => {}));
    const { container } = render(<Conversation tools={[report('r1', ASK)]} sendAction={send} />);

    await clickAdd(container);
    await clickAdd(container); // a second click before the answer arrives

    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({ name: 'chosen', surfaceId: 'which', sourceComponentId: 'go', context: { pick: ['a'] } });
    await waitFor(() => expect(screen.getByTestId('report-line').textContent).toBe('Sending your answer…'));
    expect(screen.getByTestId('report-answerable').dataset.closed).toBe('true');
  });

  it('is closed once answered, saying what was sent, until the agent draws it again', async () => {
    const send = vi.fn(async () => {});
    const { container, rerender } = render(<Conversation tools={[report('r1', ASK)]} after={[answer('b')]} sendAction={send} />);

    await clickAdd(container);
    expect(send).not.toHaveBeenCalled();
    expect(screen.getByTestId('report-line').textContent).toBe('Sent “chosen” (pick: b)');
    expect(screen.getByTestId('report-answerable').dataset.closed).toBe('true');

    const again: ChatMessage = { id: 'm3', role: 'assistant', content: '', timestamp: '2026-11-05T10:02:00Z', toolCalls: [report('r2', ASK.slice(1))] };
    rerender(<Conversation tools={[report('r1', ASK)]} after={[answer('b'), again]} sendAction={send} />);
    await clickAdd(container);
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('opens again when the answer is refused, saying why', async () => {
    const send = vi.fn(async () => {
      throw new Error("surface 'which' was answered already");
    });
    const { container } = render(<Conversation tools={[report('r1', ASK)]} sendAction={send} />);

    await clickAdd(container);

    expect(send).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId('report-answerable').dataset.closed).toBeUndefined());
    expect(screen.queryByTestId('report-line')).toBeNull();
  });

  it('reads, in the conversation, as what the person sent', () => {
    render(<MessageBubble message={answer('b')} />);
    expect(screen.getByTestId('ui-action').textContent).toBe('Sent “chosen” (pick: b) in a report');
  });

  it('is only read where a person cannot answer', async () => {
    const { container } = render(<Conversation tools={[report('r1', ASK)]} />);

    await clickAdd(container);

    expect(screen.getByTestId('report-answerable').dataset.closed).toBe('true');
  });
});

describe('a report’s Markdown', () => {
  it('is drawn as elements, never as HTML', () => {
    const { container } = render(
      <Markdown markdown={'# Prices\n- **A**: £3\n- B: `£4`\n\n<img src=x onerror=alert(1)> [shop](https://a.example) [x](javascript:alert(1))'} />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(container.querySelectorAll('li')).toHaveLength(2);
    const links = [...container.querySelectorAll('a')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://a.example']);
    expect(links[0].getAttribute('rel')).toContain('noopener');
  });
});
