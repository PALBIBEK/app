'use client';

import { Archive, Brain, ArrowCounterClockwise } from '@phosphor-icons/react';
import type { ChatMessage, SessionNoteView } from '@/lib/api/types/session.types';

const ICONS: Record<SessionNoteView['kind'], typeof Archive> = {
  compaction: Archive,
  workspace: ArrowCounterClockwise,
  memory: Brain,
};

/** A line the session adds to its chat: something changed what the agent works from. */
export function SessionNoteLine({ message }: { message: ChatMessage }): JSX.Element | null {
  const note = message.note;
  if (!note) return null;
  const Icon = ICONS[note.kind];
  return (
    <div
      data-testid="session-note"
      data-kind={note.kind}
      className="ml-10 flex items-start gap-2 py-1 text-[13px] text-ink-2"
    >
      <Icon size={14} aria-hidden="true" className="mt-[3px] shrink-0" />
      <span>{note.text}</span>
    </div>
  );
}
