/**
 * Reports the agent draws with `show_ui`: A2UI v0.9 messages (createSurface,
 * updateComponents, updateDataModel, deleteSurface), checked by the supervisor against the
 * spec's schemas before the call succeeds. A conversation's reports are one A2UI stream:
 * every successful call's messages, in order, make the surfaces. A surface is drawn where
 * the call that created it sits, always in its latest state; a later call that changes it
 * says so where it sits.
 */

import type { ToolCallView } from '../api/types/session.types';

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
