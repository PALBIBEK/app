/**
 * Reports the agent draws with `show_ui`: A2UI v0.9 messages (createSurface,
 * updateComponents, updateDataModel, deleteSurface), checked by the supervisor against the
 * spec's schemas before the call succeeds. A conversation's reports are one A2UI stream:
 * every successful call's messages, in order, make the surfaces. A surface is drawn where
 * the call that created it sits, always in its latest state; a later call that changes it
 * says so where it sits.
 *
 * A surface may ask: its button sends the agent an A2UI action, which the supervisor tells
 * the session as a user message (`UI_ACTION_PREFIX` and the action as JSON). Each drawing
 * takes one answer, so a surface answered since it was last drawn is read-only.
 */

import type { ChatMessage, ToolCallView } from '../api/types/session.types';

export const SHOW_UI = 'show_ui';

/** One A2UI message, as the agent sent it. */
export type A2uiMessage = Record<string, unknown> & { version: string };

/** A successful `show_ui` call. */
export interface ReportCall {
  id: string;
  messages: A2uiMessage[];
}

/** What one call did to the conversation's surfaces. */
export interface ReportChanges {
  created: string[];
  updated: string[];
  deleted: string[];
}

export interface ReportPlan {
  /** Each surface's place: the last call that created it. */
  owners: ReadonlyMap<string, string>;
  changes: ReadonlyMap<string, ReportChanges>;
}

const KINDS = ['createSurface', 'updateComponents', 'updateDataModel', 'deleteSurface'] as const;

function isMessage(value: unknown): value is A2uiMessage {
  return Boolean(value) && typeof value === 'object' && typeof (value as Record<string, unknown>).version === 'string';
}

/** The conversation's successful `show_ui` calls, in order. */
export function reportCalls(tools: Iterable<ToolCallView>): ReportCall[] {
  const calls: ReportCall[] = [];
  for (const tool of tools) {
    if (tool.name !== SHOW_UI || tool.status !== 'success') continue;
    const messages = tool.args?.messages;
    if (Array.isArray(messages) && messages.every(isMessage)) calls.push({ id: tool.id, messages });
  }
  return calls;
}

/** The surface a message is about. */
export function surfaceOf(message: A2uiMessage): string | null {
  for (const kind of KINDS) {
    const body = message[kind];
    if (body && typeof body === 'object') {
      const id = (body as Record<string, unknown>).surfaceId;
      return typeof id === 'string' ? id : null;
    }
  }
  return null;
}

export function reportChanges(call: ReportCall): ReportChanges {
  const created = new Set<string>();
  const deleted = new Set<string>();
  const touched = new Set<string>();
  for (const message of call.messages) {
    const surface = surfaceOf(message);
    if (surface == null) continue;
    touched.add(surface);
    if ('createSurface' in message) {
      created.add(surface);
      deleted.delete(surface);
    } else if ('deleteSurface' in message) {
      deleted.add(surface);
      created.delete(surface);
    }
  }
  const updated = [...touched].filter((s) => !created.has(s) && !deleted.has(s));
  return { created: [...created], updated, deleted: [...deleted] };
}

export function reportPlan(calls: readonly ReportCall[]): ReportPlan {
  const owners = new Map<string, string>();
  const changes = new Map<string, ReportChanges>();
  for (const call of calls) {
    const change = reportChanges(call);
    changes.set(call.id, change);
    for (const surface of change.created) owners.set(surface, call.id);
  }
  return { owners, changes };
}

/** How the message a person's action becomes begins (the supervisor's
 * `supervisor.genui.actions.ACTION_PREFIX`); the rest is the action as JSON. */
export const UI_ACTION_PREFIX = 'UI action: ';

/** A person's action on a surface, as the agent is told it. */
export interface UiAction {
  surfaceId: string;
  sourceComponentId: string;
  name: string;
  context: Record<string, unknown>;
}

/** The action a user message is, if it is one. */
export function uiActionOf(text: string): UiAction | null {
  if (!text.startsWith(UI_ACTION_PREFIX)) return null;
  try {
    const a = JSON.parse(text.slice(UI_ACTION_PREFIX.length)) as Record<string, unknown>;
    const ok = typeof a.surfaceId === 'string' && typeof a.sourceComponentId === 'string' && typeof a.name === 'string';
    const context = a.context && typeof a.context === 'object' && !Array.isArray(a.context) ? (a.context as Record<string, unknown>) : {};
    return ok ? { surfaceId: a.surfaceId as string, sourceComponentId: a.sourceComponentId as string, name: a.name as string, context } : null;
  } catch {
    return null;
  }
}

function said(value: unknown): string {
  if (Array.isArray(value)) return value.map(said).join(', ');
  if (value !== null && typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** An action in plain words: what was sent, with the values it carried. */
export function describeUiAction(action: UiAction): string {
  const values = Object.entries(action.context).map(([k, v]) => `${k}: ${said(v)}`);
  return `Sent “${action.name}”${values.length ? ` (${values.join('; ')})` : ''}`;
}

/** The surfaces answered since they were last drawn, with the answer each got, from the
 * conversation in order: an answer closes its surface; drawing it again reopens it. */
export function answeredSurfaces(messages: readonly ChatMessage[]): Map<string, UiAction> {
  const answered = new Map<string, UiAction>();
  for (const message of messages) {
    const action = message.role === 'user' ? uiActionOf(message.content) : null;
    if (action) answered.set(action.surfaceId, action);
    for (const call of reportCalls(message.toolCalls ?? [])) {
      for (const m of call.messages) {
        const surface = surfaceOf(m);
        if (surface != null) answered.delete(surface);
      }
    }
  }
  return answered;
}
