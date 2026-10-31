/**
 * Subagents as a person reads them. A subagent is a session the agent starts with a tool
 * call (`subagent`, `subagent_fork`, `subagent_continuable`): it gets one task, works with
 * the agent's tools in the agent's files, and answers. These helpers turn the calls, the
 * notices and the supervisor's listing into plain words.
 */

import type { Subagent, SubagentNoticeView, ToolCallView } from '../api/types/session.types';

export const SUBAGENT_TOOLS = new Set(['subagent', 'subagent_fork', 'subagent_continuable']);

export type SubagentKind = Subagent['kind'];
export type SubagentStatus = Subagent['status'];

/** One tool call that started a subagent, as the conversation shows it. */
export interface SubagentCallView {
  /** The subagent's session, once it has one. */
  subagentId: string | null;
  label: string | null;
  task: string;
  kind: SubagentKind;
  /** fork: it started from the agent's conversation so far. */
  forked: boolean;
  /** working: the agent waits for it. started: it went on without the agent waiting.
   * answered / failed: how the call came back. */
  state: 'working' | 'started' | 'answered' | 'failed';
  /** What it answered (or why it failed), as the agent read it. */
  answer: string | null;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function truthy(value: unknown): boolean {
  return value === true || value === 'true' || value === 'True' || value === 1;
}

/** The subagent a call started, or null when the call is not one that starts a subagent. */
export function subagentCall(tool: ToolCallView, startedId?: string): SubagentCallView | null {
  if (!SUBAGENT_TOOLS.has(tool.name)) return null;
  const args = tool.args ?? {};
  const out = (tool.output && typeof tool.output === 'object' ? tool.output : {}) as Record<string, unknown>;
  const kind: SubagentKind =
    tool.name === 'subagent_continuable' ? 'continuable' : truthy(args.run_in_background) ? 'background' : 'foreground';
  const subagentId = text(out.subagent_id) || text(out.job_id) || startedId || null;
  let state: SubagentCallView['state'];
  if (tool.status === 'running') state = 'working';
  else if (tool.status === 'error') state = 'failed';
  else state = kind === 'foreground' ? 'answered' : 'started';
  const answer = state === 'answered' ? text(out.text) : state === 'failed' ? tool.error ?? tool.explanation ?? null : null;
  return {
    subagentId,
    label: text(args.description).trim() || null,
    task: text(args.prompt).trim(),
    kind,
    forked: tool.name === 'subagent_fork',
    state,
    answer: answer || null,
  };
}

/** How a subagent runs, in a few words. */
export function kindLabel(kind: SubagentKind): string {
  switch (kind) {
    case 'foreground':
      return 'The agent waited for it';
    case 'background':
      return 'Works in the background';
    case 'continuable':
      return 'Stays for more messages';
  }
}

/** Where a subagent stands, in a few words. */
export function statusLabel(status: SubagentStatus): string {
  switch (status) {
    case 'starting':
      return 'Starting';
    case 'running':
      return 'Working';
    case 'ready':
      return 'Waiting for a message';
    case 'completed':
      return 'Answered';
    case 'failed':
      return 'Did not finish';
    case 'cancelled':
      return 'Stopped';
  }
}

/** The name a subagent goes by: its label, else the start of its task. */
export function subagentName(s: { label?: string | null; task?: string | null }): string {
  const label = (s.label ?? '').trim();
  if (label) return label;
  const task = (s.task ?? '').trim().split('\n')[0];
  if (!task) return 'Subagent';
  return task.length > 60 ? `${task.slice(0, 60)}…` : task;
}

/** A `subagent_notice` event's data, as a person reads it; null when it is not one. */
export function noticeOf(data: Record<string, unknown>): SubagentNoticeView | null {
  const subagentId = text(data.child_session_id);
  if (!subagentId) return null;
  const kind = data.kind === 'report' ? 'report' : 'settled';
  const status = data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled' ? data.status : undefined;
  // Older notices carry only the text the model read: show that.
  const own = text(data.output).trim();
  return { subagentId, kind, status, text: own || text(data.text).trim() };
}

/** What a notice says happened, after the subagent's name. */
export function noticeHeadline(notice: SubagentNoticeView): string {
  if (notice.kind === 'report') return 'sent an update';
  if (notice.status === 'failed') return 'did not finish';
  if (notice.status === 'cancelled') return 'was stopped';
  return 'finished';
}

/** The subagents in tree order: each one followed by the ones it started. The session's own
 * are those whose parent is not listed (the listing never includes the session itself). */
export function treeOrder(subagents: Subagent[]): Subagent[] {
  const ids = new Set(subagents.map((s) => s.id));
  const below = new Map<string, Subagent[]>();
  for (const s of subagents) below.set(s.parent_id, [...(below.get(s.parent_id) ?? []), s]);
  const out: Subagent[] = [];
  const walk = (s: Subagent) => {
    out.push(s);
    for (const child of below.get(s.id) ?? []) walk(child);
  };
  for (const s of subagents) if (!ids.has(s.parent_id)) walk(s);
  return out;
}

/** Whether a listing still changes on its own (a subagent is starting or working). */
export function anyActive(subagents: Subagent[]): boolean {
  return subagents.some((s) => s.status === 'starting' || s.status === 'running');
}
