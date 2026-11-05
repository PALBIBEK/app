'use client';

/**
 * The browser agent's calls in the conversation. A step reads as what it was for (its
 * opening comment), the page it ended on and the screenshot it showed, with what the
 * browser reported; its code and raw output fold away. A saved login reads as the site it
 * signed in to: the vault never shows the agent the login, and neither does the chat.
 */

import { useState } from 'react';
import { CaretDown, CaretRight, Check, CircleNotch, Globe, Key, Warning } from '@phosphor-icons/react';

import type { ToolCallView } from '@/lib/api/types/session.types';
import { browserStep, shortUrl, vaultLine, type BrowserEventView } from '@/lib/session/browserSteps';
import { clip } from '@/lib/session/explain';
import { cn } from '@/lib/utils';

function Status({ tool, failed }: { tool: ToolCallView; failed: boolean }): JSX.Element {
  if (tool.status === 'running') {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-ink-2">
        <CircleNotch size={13} className="animate-spin text-accent-600" /> Running
      </span>
    );
  }
  if (tool.status === 'error' || failed) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-ink-2">
        <span className="h-2 w-2 rotate-45 bg-bad" aria-hidden="true" /> {tool.status === 'error' ? 'Refused' : 'Failed'}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-ink-2">
      <Check size={13} weight="bold" className="text-ink" /> Done
    </span>
  );
}

function EventBadge({ event }: { event: BrowserEventView }): JSX.Element {
  return (
    <li
      data-testid="browser-event"
      className={cn(
        'max-w-full truncate rounded-full border px-2 py-px text-[12px]',
        event.kind === 'dialog' ? 'border-line-strong text-ink-2' : 'border-bad/30 bg-bad/5 text-bad',
      )}
      title={event.text}
    >
      {event.text}
      {event.times > 1 ? ` ×${event.times}` : ''}
    </li>
  );
}

/** One `browser_exec` call. */
export function BrowserStepCard({ tool }: { tool: ToolCallView }): JSX.Element {
  const [open, setOpen] = useState(false);
  const step = browserStep(tool);
  const failed = step.error != null || step.timedOut || (step.exitCode != null && step.exitCode !== 0);
  const problem = tool.status === 'error' ? tool.explanation ?? tool.error : step.error ?? (step.timedOut ? 'The step ran out of time; the browser kept its state.' : null);
  return (
    <div data-testid="browser-step" className="overflow-hidden rounded-[10px] border border-line bg-surface text-sm">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={`${open ? 'Collapse' : 'Expand'} the browser step: ${step.title}`}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left hover:bg-canvas/60"
      >
        <Globe size={15} className="shrink-0 text-ink-2" aria-hidden="true" />
        <span className="font-medium text-ink">{step.title}</span>
        {step.url ? (
          <span data-testid="browser-url" className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-2" title={step.url}>
            {shortUrl(step.url)}
          </span>
        ) : (
          <span className="flex-1" />
        )}
        <Status tool={tool} failed={failed} />
        {open ? <CaretDown size={14} className="text-ink-2" /> : <CaretRight size={14} className="text-ink-2" />}
      </button>
      {problem ? (
        <p data-testid="tool-explanation" className="border-t border-line-soft px-3 py-2 text-[13px] text-ink">
          {problem}
          {tool.code ? <span className="ml-1.5 font-mono text-[11px] text-ink-3">{tool.code}</span> : null}
        </p>
      ) : null}
      {step.freshBrowser ? (
        <p data-testid="browser-fresh" className="flex items-center gap-1.5 border-t border-line-soft bg-canvas px-3 py-1.5 text-[12.5px] text-warn">
          <Warning size={13} aria-hidden="true" /> A new browser started: tabs and sign-ins from earlier steps are gone.
        </p>
      ) : null}
      {step.events.length ? (
        <ul aria-label="What the browser reported" className="flex flex-wrap gap-1.5 border-t border-line-soft px-3 py-2">
          {step.events.map((event, i) => (
            <EventBadge key={i} event={event} />
          ))}
        </ul>
      ) : null}
      {step.screenshot ? (
        // A data URL from the step's own result: nothing for next/image to optimise.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          data-testid="browser-screenshot"
          src={step.screenshot}
          alt={`The page after: ${step.title}`}
          className="block max-h-72 w-full border-t border-line-soft bg-canvas object-contain object-top"
        />
      ) : null}
      {open ? (
        <div className="border-t border-line-soft">
          {step.code ? <pre className="overflow-x-auto bg-canvas px-3.5 py-2.5 font-mono text-[12px] leading-relaxed text-ink-2">{step.code}</pre> : null}
          {step.output ? (
            <pre data-testid="tool-output" className="max-h-80 overflow-auto border-t border-line-soft bg-[#F9FAFB] px-3.5 py-2.5 font-mono text-[12.5px] leading-[1.7] text-ink">
              {clip(step.output)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** One `browser_vault` call. */
export function VaultCard({ tool }: { tool: ToolCallView }): JSX.Element {
  const line = vaultLine(tool);
  return (
    <div data-testid="vault-card" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[10px] border border-line bg-surface px-3 py-2 text-sm">
      <Key size={15} className="shrink-0 text-ink-2" aria-hidden="true" />
      <span className={cn('min-w-0 flex-1', line.failed ? 'text-bad' : 'text-ink')}>{line.text}</span>
      <Status tool={tool} failed={line.failed} />
    </div>
  );
}
