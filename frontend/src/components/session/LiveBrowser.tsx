'use client';

/**
 * The agent's browser, live, for the person: watch it at any time, and take control when the
 * agent asks for them (a CAPTCHA, a code sent to them, a payment they confirm), while it is
 * not working. Their clicks, typing, keys and scrolling reach the page; handing back tells
 * the agent what they did. The rail shows the way in once the agent has used a browser.
 */

import { useEffect, useRef, useState } from 'react';
import { ArrowUUpLeft, Browser, CircleNotch, HandPointing, X } from '@phosphor-icons/react';

import { Card } from '@/components/ui';
import { shortUrl } from '@/lib/session/browserSteps';
import { handBackMessage, keyInput, pagePoint } from '@/lib/session/liveBrowser';
import { useLiveBrowser } from '@/lib/session/useLiveBrowser';
import { cn } from '@/lib/utils';

const SCROLL_EVERY_MS = 150;

/** The rail's way in to the live view. */
export function LiveBrowserSection({
  sessionId,
  agentWorking,
  onHandBack,
}: {
  sessionId: string;
  agentWorking: boolean;
  onHandBack: (message: string) => Promise<void>;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <Card className="flex flex-col gap-2.5 px-[18px] py-4" data-testid="rail-browser">
      <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold">
        <Browser size={14} aria-hidden="true" /> Browser
      </h2>
      <p className="text-[13px] text-ink-2">Watch the agent’s browser live, and take control when it asks you to.</p>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="self-start rounded-lg border border-line-strong bg-surface px-3 py-1.5 text-[13px] font-medium hover:bg-canvas"
      >
        Open live view
      </button>
      {open ? <LiveBrowserDialog sessionId={sessionId} agentWorking={agentWorking} onHandBack={onHandBack} onClose={() => setOpen(false)} /> : null}
    </Card>
  );
}

export function LiveBrowserDialog({
  sessionId,
  agentWorking,
  onHandBack,
  onClose,
}: {
  sessionId: string;
  agentWorking: boolean;
  onHandBack: (message: string) => Promise<void>;
  onClose: () => void;
}): JSX.Element {
  const { screen, error, send } = useLiveBrowser(sessionId, true);
  const [control, setControl] = useState(false);
  const [note, setNote] = useState('');
  const [handing, setHanding] = useState(false);
  const page = useRef<HTMLDivElement>(null);
  const scrolled = useRef({ at: 0, dy: 0, dx: 0 });
  const controlling = control && !agentWorking;

  useEffect(() => {
    if (agentWorking) setControl(false); // the agent took the browser back: watch only
  }, [agentWorking]);
  useEffect(() => {
    if (controlling) page.current?.focus();
  }, [controlling]);

  const point = (e: React.MouseEvent | React.WheelEvent): { x: number; y: number } | null => {
    if (!screen) return null;
    const box = e.currentTarget.getBoundingClientRect();
    return pagePoint({ x: e.clientX - box.left, y: e.clientY - box.top }, { width: box.width, height: box.height }, screen);
  };

  async function handBack(): Promise<void> {
    setHanding(true);
    try {
      await onHandBack(handBackMessage(screen, note));
      setControl(false);
      onClose();
    } finally {
      setHanding(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4" role="presentation">
      <div role="dialog" aria-modal="true" aria-label="The agent’s browser, live" className="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-card border border-line bg-surface shadow-xl">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2.5">
          <span className="relative flex h-2 w-2" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-bad/60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-bad" />
          </span>
          <span className="text-sm font-semibold">Live</span>
          <span data-testid="live-url" className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-2" title={screen?.url}>
            {screen ? `${screen.title} · ${shortUrl(screen.url)}` : 'Connecting…'}
          </span>
          <button type="button" onClick={onClose} aria-label="Close the live view" className="rounded-md p-1 text-ink-3 hover:bg-line-soft hover:text-ink">
            <X size={16} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto bg-canvas p-3">
          {screen ? (
            <div
              ref={page}
              data-testid="live-page"
              tabIndex={controlling ? 0 : -1}
              aria-label={controlling ? 'The page: click, type and scroll here' : 'The page'}
              onKeyDown={(e) => {
                if (!controlling) return;
                const input = keyInput(e);
                if (input) {
                  e.preventDefault();
                  send(input);
                }
              }}
              className={cn('relative mx-auto w-fit outline-none', controlling && 'cursor-pointer ring-2 ring-accent-500 ring-offset-2')}
            >
              {/* A data URL the supervisor sent: nothing for next/image to optimise. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                data-testid="live-screen"
                src={`data:image/jpeg;base64,${screen.image}`}
                alt={`The agent’s browser: ${screen.title}`}
                draggable={false}
                onClick={(e) => {
                  const at = controlling ? point(e) : null;
                  if (at) send({ kind: 'click', ...at });
                }}
                onWheel={(e) => {
                  const at = controlling ? point(e) : null;
                  if (!at) return;
                  const s = scrolled.current;
                  s.dx += e.deltaX;
                  s.dy += e.deltaY;
                  if (Date.now() - s.at < SCROLL_EVERY_MS) return;
                  send({ kind: 'scroll', ...at, dx: Math.round(s.dx), dy: Math.round(s.dy) });
                  scrolled.current = { at: Date.now(), dx: 0, dy: 0 };
                }}
                className="block max-h-[70vh] w-auto max-w-full select-none"
                style={{ aspectRatio: `${screen.width} / ${screen.height}` }}
              />
            </div>
          ) : (
            <p className="flex items-center justify-center gap-2 py-16 text-[13px] text-ink-2">
              {error ? error : (<><CircleNotch size={14} className="animate-spin" /> Looking at the browser…</>)}
            </p>
          )}
          {screen && error ? (
            <p role="alert" data-testid="live-error" className="mx-auto mt-2 w-fit text-[13px] text-bad">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          {agentWorking ? (
            <p data-testid="live-watching" className="text-[13px] text-ink-2">
              The agent is working: you can watch. You can take control once its turn ends.
            </p>
          ) : controlling ? (
            <>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="What did you do? (optional, the agent reads it)"
                aria-label="What you did, for the agent"
                className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong px-3 text-[13px]"
              />
              <button
                type="button"
                onClick={() => void handBack()}
                disabled={handing}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent-500 px-3.5 text-[13px] font-medium text-white hover:bg-accent-600 disabled:opacity-60"
              >
                <ArrowUUpLeft size={14} aria-hidden="true" /> Hand back to the agent
              </button>
            </>
          ) : (
            <>
              <p className="flex-1 text-[13px] text-ink-2">Take control to click, type and scroll in this browser yourself.</p>
              <button
                type="button"
                onClick={() => setControl(true)}
                disabled={!screen}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent-500 px-3.5 text-[13px] font-medium text-white hover:bg-accent-600 disabled:opacity-60"
              >
                <HandPointing size={14} aria-hidden="true" /> Take control
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
