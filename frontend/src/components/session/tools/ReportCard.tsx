'use client';

/**
 * A `show_ui` call: the report it drew, where it sits in the conversation. A surface is
 * drawn by the call that created it, in its latest state; a later call that changes or
 * removes it says so in a line. A refused call reads like any refused call.
 *
 * A surface that asks takes one answer per drawing: from the click until the agent draws it
 * again, its inputs are closed, and the line under it says what was sent.
 */

import { useState } from 'react';
import dynamic from 'next/dynamic';
import { ArrowDown, ArrowUp, ChartBar, CheckCircle, CircleNotch, Code, Trash } from '@phosphor-icons/react';

import type { ToolCallView } from '@/lib/api/types/session.types';
import { describeUiAction } from '@/lib/session/reports';
import type { ReportSurfaceModel, Reports } from '@/lib/session/useReports';
import type { SurfaceAnswers } from '@/lib/session/useSurfaceAnswers';
import { cn } from '@/lib/utils';

import { ToolCard } from '../parts';

function Drawing(): JSX.Element {
  return (
    <div className="flex items-center gap-2 px-3 py-3 text-[13px] text-ink-2">
      <CircleNotch size={13} className="animate-spin text-accent-600" aria-hidden="true" /> Drawing the report…
    </div>
  );
}

const ReportSurface = dynamic(() => import('./ReportSurface'), { ssr: false, loading: Drawing });

function Line({ icon, children }: { icon: JSX.Element; children: React.ReactNode }): JSX.Element {
  return (
    <p data-testid="report-line" className="flex items-center gap-2 px-3 py-2 text-[13px] text-ink-2">
      {icon}
      <span>{children}</span>
    </p>
  );
}

/** Inputs a closed surface keeps on show but no longer takes (its links still open). */
const CLOSED = '[&_button]:pointer-events-none [&_button]:opacity-60 [&_input]:pointer-events-none [&_select]:pointer-events-none [&_textarea]:pointer-events-none';

function Surface({ model, answers }: { model: ReportSurfaceModel; answers: SurfaceAnswers }): JSX.Element {
  const answer = answers.of(model.id);
  const closed = !answers.canAnswer || answer.state !== 'open';
  return (
    <div data-testid="report-answerable" data-closed={closed || undefined} aria-disabled={closed || undefined}>
      <div className={cn('px-4 py-3', closed && CLOSED)}>
        <ReportSurface surface={model} />
      </div>
      {answer.state === 'sending' ? (
        <Line icon={<CircleNotch size={13} className="animate-spin text-accent-600" aria-hidden="true" />}>Sending your answer…</Line>
      ) : answer.state === 'answered' ? (
        <Line icon={<CheckCircle size={13} weight="fill" className="text-good" aria-hidden="true" />}>{describeUiAction(answer.action)}</Line>
      ) : null}
    </div>
  );
}

export function ReportCard({ tool, reports, answers }: { tool: ToolCallView; reports: Reports; answers: SurfaceAnswers }): JSX.Element {
  const [raw, setRaw] = useState(false);
  if (tool.status === 'error') return <ToolCard tool={tool} />;
  if (tool.status === 'running') return <Drawing />;
  const changes = reports.changes(tool.id);
  const failure = reports.failure(tool.id);
  const drawn = changes?.created.filter((surface) => reports.owns(tool.id, surface)) ?? [];
  const moved = changes?.created.filter((surface) => !reports.owns(tool.id, surface)) ?? [];
  return (
    <div data-testid="report-card" className="overflow-hidden rounded-[10px] border border-line bg-surface text-sm">
      <div className="flex items-center gap-2 border-b border-line-soft px-3 py-1.5 text-[12.5px] text-ink-3">
        <ChartBar size={14} aria-hidden="true" />
        <span className="font-medium">Report</span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => setRaw((r) => !r)}
          aria-expanded={raw}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-canvas hover:text-ink"
        >
          <Code size={12} aria-hidden="true" /> {raw ? 'Hide' : 'Show'} A2UI
        </button>
      </div>
      {failure ? (
        <p data-testid="tool-explanation" className="px-3 py-2 text-[13px] text-ink">
          This report could not be drawn: {failure}
        </p>
      ) : null}
      {drawn.map((surface) => {
        if (!reports.ready) return <Drawing key={surface} />;
        const model = reports.surface(surface);
        return model ? (
          <Surface key={surface} model={model} answers={answers} />
        ) : (
          <Line key={surface} icon={<Trash size={13} aria-hidden="true" />}>
            This report was removed later in the conversation.
          </Line>
        );
      })}
      {moved.map((surface) => (
        <Line key={surface} icon={<ArrowDown size={13} aria-hidden="true" />}>
          Drawn again further down.
        </Line>
      ))}
      {changes?.updated.map((surface) => (
        <Line key={surface} icon={<ArrowUp size={13} aria-hidden="true" />}>
          Updated the report above.
        </Line>
      ))}
      {changes?.deleted.map((surface) => (
        <Line key={surface} icon={<Trash size={13} aria-hidden="true" />}>
          Removed a report from the conversation.
        </Line>
      ))}
      {raw ? (
        <pre data-testid="report-a2ui" className="max-h-80 overflow-auto border-t border-line-soft bg-canvas px-3.5 py-2.5 font-mono text-[12px] leading-relaxed text-ink-2">
          {JSON.stringify(tool.args?.messages, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
