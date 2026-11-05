'use client';

/**
 * A conversation's reports, drawn: one A2UI `MessageProcessor` on the basic catalog, fed
 * every successful `show_ui` call's messages in order (./reports). The renderer is loaded
 * only once a conversation has a report, and only in the browser: its components are Web
 * Components.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactCatalogComponent } from '@a2ui/react/v0_9';
import type { ActionPayload, MessageProcessor, ProcessableMessage, SurfaceModel } from '@a2ui/web_core/v0_9';

import { reportPlan, type ReportCall, type ReportChanges } from './reports';

type Processor = MessageProcessor<ReactCatalogComponent>;
export type ReportSurfaceModel = SurfaceModel<ReactCatalogComponent>;

export interface Reports {
  /** The renderer is loaded and has every report so far. */
  ready: boolean;
  /** What a `show_ui` call did, by its id. */
  changes(callId: string): ReportChanges | undefined;
  /** Whether the call is where `surfaceId` is drawn (it created it last). */
  owns(callId: string, surfaceId: string): boolean;
  /** The surface as it is now, if it is still there. */
  surface(surfaceId: string): ReportSurfaceModel | undefined;
  /** Why a call's messages could not be drawn, by its id. */
  failure(callId: string): string | undefined;
}

/** What a person did on a surface: a button's event, with the values it sends. */
export type ReportAction = ActionPayload;

async function newProcessor(): Promise<(onAction: (action: ReportAction) => void) => Processor> {
  const [{ MessageProcessor }, { basicCatalog }] = await Promise.all([
    import('@a2ui/web_core/v0_9'),
    import('@a2ui/web_core/v0_9/basic_catalog'),
  ]);
  // The basic catalog's components are Web Components, which a React catalog holds too.
  return (onAction) => new MessageProcessor([basicCatalog], onAction) as unknown as Processor;
}

interface Fed {
  processor: Processor;
  ids: string[];
  failures: Map<string, string>;
}

function extends_(ids: readonly string[], prefix: readonly string[]): boolean {
  return prefix.length <= ids.length && prefix.every((id, i) => ids[i] === id);
}

/** `calls` drawn; a button's event goes to `onAction` (the latest one given). */
export function useReports(calls: readonly ReportCall[], onAction?: (action: ReportAction) => void): Reports {
  const acted = useRef(onAction);
  acted.current = onAction;
  const key = calls.map((c) => c.id).join('\n');
  // A call's messages never change once it succeeded: the ids say which calls these are.
  const stable = useMemo(() => calls, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const plan = useMemo(() => reportPlan(stable), [stable]);
  const [create, setCreate] = useState<((onAction: (action: ReportAction) => void) => Processor) | null>(null);
  const fed = useRef<Fed | null>(null);
  /** What has been drawn, as of the last calls fed (a new object each time, to redraw). */
  const [drawn, setDrawn] = useState<{ fed: Fed; count: number } | null>(null);
  const wanted = stable.length > 0;

  useEffect(() => {
    if (!wanted || create) return;
    let live = true;
    void newProcessor().then((make) => live && setCreate(() => make));
    return () => {
      live = false;
    };
  }, [wanted, create]);

  useEffect(() => {
    if (!create) return;
    const ids = stable.map((c) => c.id);
    let current = fed.current;
    if (!current || !extends_(ids, current.ids)) {
      current?.processor.dispose(); // the conversation was replaced (a fork, a reload): start over
      current = { processor: create((action) => acted.current?.(action)), ids: [], failures: new Map() };
    }
    for (const call of stable.slice(current.ids.length)) {
      try {
        // Checked by the supervisor against the spec's own schemas before the call succeeded.
        current.processor.processMessages(call.messages as unknown as ProcessableMessage[]);
      } catch (error) {
        current.failures.set(call.id, error instanceof Error ? error.message : String(error));
      }
      current.ids.push(call.id);
    }
    fed.current = current;
    setDrawn({ fed: current, count: current.ids.length });
  }, [create, stable]);

  useEffect(
    () => () => {
      fed.current?.processor.dispose();
      fed.current = null;
    },
    [],
  );

  return useMemo<Reports>(
    () => ({
      ready: drawn !== null && drawn.count === stable.length,
      changes: (callId) => plan.changes.get(callId),
      owns: (callId, surfaceId) => plan.owners.get(surfaceId) === callId,
      surface: (surfaceId) => drawn?.fed.processor.model.surfacesMap.get(surfaceId),
      failure: (callId) => drawn?.fed.failures.get(callId),
    }),
    [drawn, plan, stable],
  );
}
