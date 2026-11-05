'use client';

/**
 * How each tool call is drawn: a card per tool name, with the generic `ToolCard` for every
 * other tool (CopilotKit's tool-render pattern: a renderer is looked up by the call's name).
 * `useToolRenderer` gives a conversation its renderer; it holds the conversation's reports,
 * which every `show_ui` call in it draws together, and the person's answers to them.
 */

import { useCallback, useMemo } from 'react';

import type { ChatMessage, ToolCallView } from '@/lib/api/types/session.types';
import { BROWSER_EXEC, BROWSER_VAULT } from '@/lib/session/browserSteps';
import { reportCalls, SHOW_UI } from '@/lib/session/reports';
import { UPDATE_TASK } from '@/lib/session/task';
import { SUBAGENT_TOOLS } from '@/lib/session/subagents';
import { useReports, type ReportAction, type Reports } from '@/lib/session/useReports';
import { useSurfaceAnswers, type SurfaceAnswers } from '@/lib/session/useSurfaceAnswers';

import { ToolCard } from '../parts';
import { SubagentCallCard } from '../subagents';
import { BrowserStepCard, VaultCard } from './BrowserCards';
import { ReportCard } from './ReportCard';
import { TaskCard } from './TaskCard';

/** What a card may need beyond its call. */
export interface ToolRenderContext {
  /** The (product) session the conversation belongs to. */
  sessionId: string;
  /** The subagent each running subagent call started, by call id. */
  subagentOf: ReadonlyMap<string, string>;
  reports: Reports;
  answers: SurfaceAnswers;
}

type ToolCardComponent = (props: { tool: ToolCallView; context: ToolRenderContext }) => JSX.Element | null;

const subagentCard: ToolCardComponent = ({ tool, context }) => (
  <SubagentCallCard tool={tool} sessionId={context.sessionId} startedId={context.subagentOf.get(tool.id)} />
);

const CARDS: Readonly<Record<string, ToolCardComponent>> = {
  [BROWSER_EXEC]: ({ tool }) => <BrowserStepCard tool={tool} />,
  [BROWSER_VAULT]: ({ tool }) => <VaultCard tool={tool} />,
  [UPDATE_TASK]: ({ tool }) => <TaskCard tool={tool} />,
  [SHOW_UI]: ({ tool, context }) => <ReportCard tool={tool} reports={context.reports} answers={context.answers} />,
  ...Object.fromEntries([...SUBAGENT_TOOLS].map((name) => [name, subagentCard])),
};

export function ToolCall({ tool, context }: { tool: ToolCallView; context: ToolRenderContext }): JSX.Element | null {
  const Card = CARDS[tool.name];
  return Card ? <Card tool={tool} context={context} /> : <ToolCard tool={tool} />;
}

/** A conversation's tool renderer, for `MessageBubble`'s `renderTool`: its reports are drawn
 * from every call in `messages` and `pendingTools`, in order. With `sendAction`, a person can
 * answer a report that asks; without it, reports are only read. */
export function useToolRenderer({
  sessionId,
  subagentOf,
  messages,
  pendingTools = [],
  sendAction,
}: {
  sessionId: string;
  subagentOf: ReadonlyMap<string, string>;
  messages: readonly ChatMessage[];
  pendingTools?: readonly ToolCallView[];
  sendAction?: (action: ReportAction) => Promise<void>;
}): (tool: ToolCallView) => JSX.Element {
  const answers = useSurfaceAnswers(messages, sendAction);
  const reports = useReports(reportCalls([...messages.flatMap((m) => m.toolCalls ?? []), ...pendingTools]), answers.onAction);
  const context = useMemo<ToolRenderContext>(
    () => ({ sessionId, subagentOf, reports, answers }),
    [sessionId, subagentOf, reports, answers],
  );
  return useCallback((tool: ToolCallView) => <ToolCall key={tool.id} tool={tool} context={context} />, [context]);
}
