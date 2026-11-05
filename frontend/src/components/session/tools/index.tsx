'use client';

/**
 * How each tool call is drawn: a card per tool name, with the generic `ToolCard` for every
 * other tool (CopilotKit's tool-render pattern: a renderer is looked up by the call's name).
 * `useToolRenderer` gives a conversation its renderer; it holds the conversation's reports,
 * which every `show_ui` call in it draws together.
 */

import { useCallback, useMemo } from 'react';

import type { ToolCallView } from '@/lib/api/types/session.types';
import { BROWSER_EXEC, BROWSER_VAULT } from '@/lib/session/browserSteps';
import { reportCalls, SHOW_UI } from '@/lib/session/reports';
import { SUBAGENT_TOOLS } from '@/lib/session/subagents';
import { useReports, type Reports } from '@/lib/session/useReports';

import { ToolCard } from '../parts';
import { SubagentCallCard } from '../subagents';
import { BrowserStepCard, VaultCard } from './BrowserCards';
import { ReportCard } from './ReportCard';

/** What a card may need beyond its call. */
export interface ToolRenderContext {
  /** The (product) session the conversation belongs to. */
  sessionId: string;
  /** The subagent each running subagent call started, by call id. */
  subagentOf: ReadonlyMap<string, string>;
  reports: Reports;
}

type ToolCardComponent = (props: { tool: ToolCallView; context: ToolRenderContext }) => JSX.Element | null;

const subagentCard: ToolCardComponent = ({ tool, context }) => (
  <SubagentCallCard tool={tool} sessionId={context.sessionId} startedId={context.subagentOf.get(tool.id)} />
);

const CARDS: Readonly<Record<string, ToolCardComponent>> = {
  [BROWSER_EXEC]: ({ tool }) => <BrowserStepCard tool={tool} />,
  [BROWSER_VAULT]: ({ tool }) => <VaultCard tool={tool} />,
  [SHOW_UI]: ({ tool, context }) => <ReportCard tool={tool} reports={context.reports} />,
  ...Object.fromEntries([...SUBAGENT_TOOLS].map((name) => [name, subagentCard])),
};

export function ToolCall({ tool, context }: { tool: ToolCallView; context: ToolRenderContext }): JSX.Element | null {
  const Card = CARDS[tool.name];
  return Card ? <Card tool={tool} context={context} /> : <ToolCard tool={tool} />;
}

/** A conversation's tool renderer, for `MessageBubble`'s `renderTool`. `tools` is every
 * call in the conversation, in order (its reports are drawn from them). */
export function useToolRenderer({
  sessionId,
  subagentOf,
  tools,
}: {
  sessionId: string;
  subagentOf: ReadonlyMap<string, string>;
  tools: readonly ToolCallView[];
}): (tool: ToolCallView) => JSX.Element {
  const reports = useReports(reportCalls(tools));
  const context = useMemo<ToolRenderContext>(() => ({ sessionId, subagentOf, reports }), [sessionId, subagentOf, reports]);
  return useCallback((tool: ToolCallView) => <ToolCall key={tool.id} tool={tool} context={context} />, [context]);
}
