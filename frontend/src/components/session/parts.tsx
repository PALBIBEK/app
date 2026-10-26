'use client';

/**
 * The pieces a conversation is drawn with: messages, tool calls, subagents, approvals.
 */

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import {
  CaretDown,
  CaretRight,
  Check,
  CircleNotch,
  Code,
  Copy,
  FileText,
  GitBranch,
  MagnifyingGlass,
  PencilSimple,
  ShieldWarning,
  TerminalWindow,
  Warning,
  Wrench,
} from '@phosphor-icons/react';

import type { ChatMessage, ToolCallView } from '@/lib/api/types/session.types';
import { clip } from '@/lib/session/explain';
import { displaySubagentProvider, type SubagentChipView } from '@/lib/session/wireEventReducer';
import { cn } from '@/lib/utils';

const messageVariants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.2 } },
  exit: { opacity: 0, transition: { duration: 0.12 } },
};

function show(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  // A command's result: its output streams, not the wrapper object.
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>;
    if ('stdout' in v || 'stderr' in v) {
      return [v.stdout, v.stderr].filter((x) => typeof x === 'string' && x).join('\n') || `exit ${String(v.exit_code ?? '')}`;
    }
  }
  return JSON.stringify(value, null, 2);
}

/** The one argument that says what a call did (a path, a command, a pattern), else the args in brief. */
function argSummary(args: Record<string, unknown> | null | undefined): string {
  if (!args) return '';
  for (const key of ['command', 'file_path', 'path', 'pattern', 'query', 'url', 'name']) {
    const v = args[key];
    if (typeof v === 'string' && v) return v;
  }
  const s = Object.keys(args).length ? JSON.stringify(args) : '';
  return s.length > 80 ? `${s.slice(0, 80)}…` : s;
}

function ToolIcon({ name }: { name: string }): JSX.Element {
  const n = name.toLowerCase();
  const props = { size: 15, className: 'shrink-0 text-ink-2', 'aria-hidden': true } as const;
  if (/(bash|shell|exec|command|run)/.test(n)) return <TerminalWindow {...props} />;
  if (/(grep|search|find|glob)/.test(n)) return <MagnifyingGlass {...props} />;
  if (/(edit|write|patch)/.test(n)) return <PencilSimple {...props} />;
  if (/(read|file|cat|view)/.test(n)) return <FileText {...props} />;
  return <Wrench {...props} />;
}

function exitCode(output: unknown): number | null {
  if (output && typeof output === 'object' && 'exit_code' in (output as Record<string, unknown>)) {
    const c = (output as Record<string, unknown>).exit_code;
    return typeof c === 'number' ? c : null;
  }
  return null;
}

/** A tool call: what it was asked to do, what came back, and a refusal in plain words. */
export function ToolCard({ tool }: { tool: ToolCallView }): JSX.Element {
  const [open, setOpen] = useState(false);
  const summary = argSummary(tool.args);
  const output = show(tool.output);
  const code = exitCode(tool.output);
  return (
    <div data-testid="tool-card" className="overflow-hidden rounded-[10px] border border-line bg-surface text-sm">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={`${open ? 'Collapse' : 'Expand'} ${tool.name} output`}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left hover:bg-canvas/60"
      >
        <ToolIcon name={tool.name} />
        <span className="font-mono text-[13px] font-medium text-ink">{tool.name}</span>
        {summary ? <span className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink-2">{summary}</span> : <span className="flex-1" />}
        <span className="inline-flex items-center gap-1 text-xs text-ink-2">
          {tool.status === 'running' ? (
            <>
              <CircleNotch size={13} className="animate-spin text-accent-600" /> Running
            </>
          ) : tool.status === 'error' ? (
            <>
              <span className="h-2 w-2 rotate-45 bg-bad" aria-hidden="true" /> Refused
            </>
          ) : (
            <>
              <Check size={13} weight="bold" className="text-ink" /> {code != null ? `Exit ${code}` : 'Done'}
            </>
          )}
        </span>
        {open ? <CaretDown size={14} className="text-ink-2" /> : <CaretRight size={14} className="text-ink-2" />}
      </button>
      {tool.status === 'error' ? (
        <p data-testid="tool-explanation" className="border-t border-line-soft px-3 py-2 text-[13px] text-ink">
          {tool.explanation ?? tool.error}
          {tool.code ? <span className="ml-1.5 font-mono text-[11px] text-ink-3">{tool.code}</span> : null}
        </p>
      ) : null}
      {open ? (
        <div className="space-y-0 border-t border-line-soft">
          {tool.args && Object.keys(tool.args).length ? (
            <pre className="overflow-x-auto bg-canvas px-3.5 py-2.5 font-mono text-[12px] leading-relaxed text-ink-2">{JSON.stringify(tool.args, null, 2)}</pre>
          ) : null}
          {output ? (
            <pre data-testid="tool-output" className="max-h-80 overflow-auto border-t border-line-soft bg-[#F9FAFB] px-3.5 py-2.5 font-mono text-[12.5px] leading-[1.7] text-ink">
              {clip(output)}
            </pre>
          ) : null}
        </div>
      ) : output && tool.status === 'success' ? (
        <p data-testid="tool-output" className="truncate border-t border-line-soft bg-[#F9FAFB] px-3 py-1.5 font-mono text-xs text-ink-2">
          {output.split('\n')[0]}
        </p>
      ) : null}
    </div>
  );
}

export function SubagentChip({ chip, sessionId }: { chip: SubagentChipView; sessionId?: string }): JSX.Element {
  const provider = displaySubagentProvider(chip.provider);
  const href =
    chip.child_session_id && sessionId
      ? `/subagents/${encodeURIComponent(chip.child_session_id)}?session=${encodeURIComponent(sessionId)}`
      : '/subagents';
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[10px] border border-dashed border-line-strong px-3 py-2 text-sm">
      {chip.status === 'running' ? <CircleNotch size={15} className="animate-spin text-accent-600" /> : <GitBranch size={15} className="text-ink-2" />}
      <span className="font-medium">
        Subagent {chip.status}
        {chip.label ? ` · ${chip.label}` : ''}
      </span>
      <span className="text-[13px] text-ink-2">
        {chip.mode.replace(/_/g, ' ')} · {provider.label}
        {chip.stop_reason ? ` · ${chip.stop_reason}` : ''}
      </span>
      <span className="flex-1" />
      <Link href={href} className="text-[13px] font-medium text-accent-700 hover:text-accent-600">
        Open
      </Link>
    </div>
  );
}

/** Seconds left until `deadline` (epoch ms), ticking. */
function useSecondsLeft(deadline?: number): number | undefined {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (deadline == null) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [deadline]);
  return deadline == null ? undefined : Math.max(0, Math.round((deadline - now) / 1000));
}

const ALLOW = /^(approve|allow|yes|y|ok)$/i;
const DENY = /^(deny|reject|no|n)$/i;

/** The question a paused run asks: accent-bordered, the one thing on the page that needs you. */
export function ApprovalCard({
  prompt,
  options,
  deadline,
  onTimeout,
  onAnswer,
}: {
  prompt: string;
  options: string[];
  deadline?: number;
  onTimeout?: string;
  onAnswer: (choice: string) => void;
}): JSX.Element {
  const left = useSecondsLeft(deadline);
  const allow = options.find((o) => ALLOW.test(o)) ?? options[0];
  const deny = options.find((o) => DENY.test(o)) ?? options[options.length - 1];
  const others = options.filter((o) => o !== allow && o !== deny);
  return (
    <div data-testid="approval-banner" role="group" aria-label="Approval required" className="overflow-hidden rounded-card border border-accent-500 bg-surface">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 bg-accent-50 px-4 py-3 text-accent-700">
        <ShieldWarning size={16} aria-hidden="true" />
        <span className="font-semibold">Approval required</span>
        <span className="flex-1" />
        {left != null ? (
          <span data-testid="approval-countdown" className="font-mono text-xs">
            {left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left` : 'Timing out'}
            {onTimeout ? ` · then it is ${onTimeout === 'allow' ? 'allowed' : 'denied'}` : ''}
          </span>
        ) : null}
      </div>
      <div className="flex flex-col gap-3.5 p-4">
        <p className="whitespace-pre-wrap break-words">{prompt}</p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="approval-approve"
            onClick={() => onAnswer(allow)}
            className="h-11 rounded-lg bg-accent-500 px-[18px] font-medium text-white hover:bg-accent-600"
          >
            {ALLOW.test(allow) ? 'Approve once' : allow}
          </button>
          {others.map((o) => (
            <button key={o} type="button" onClick={() => onAnswer(o)} className="h-11 rounded-lg border border-line-strong bg-surface px-4 font-medium hover:bg-canvas">
              {o}
            </button>
          ))}
          <span className="flex-1" />
          <button
            type="button"
            data-testid="approval-deny"
            onClick={() => onAnswer(deny)}
            className="h-11 rounded-lg border border-line-strong bg-surface px-4 font-medium hover:bg-canvas"
          >
            {DENY.test(deny) ? 'Deny' : deny}
          </button>
        </div>
      </div>
    </div>
  );
}

function Avatar({ user, initials }: { user: boolean; initials?: string }): JSX.Element {
  return user ? (
    <div aria-hidden="true" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-line text-[11px] font-semibold">
      {initials || 'You'.slice(0, 1)}
    </div>
  ) : (
    <div aria-hidden="true" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-ink text-white">
      <TerminalWindow size={14} />
    </div>
  );
}

export function MessageBubble({
  message,
  queued,
  cancelled,
  onCancel,
  onFork,
  agentName,
  userInitials,
}: {
  message: ChatMessage;
  queued?: boolean;
  /** Its run was cancelled before it started: the message never ran. */
  cancelled?: boolean;
  /** Cancel a queued message's run. */
  onCancel?: () => void;
  onFork?: () => void;
  agentName?: string;
  userInitials?: string;
}): JSX.Element {
  const isUser = message.role === 'user';
  const [copied, setCopied] = useState(false);
  const ts = new Date(message.timestamp);
  return (
    <motion.article variants={messageVariants} initial="hidden" animate="visible" exit="exit" className="group/msg flex items-start gap-3">
      <Avatar user={isUser} initials={userInitials} />
      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-x-2 text-[12.5px] text-ink-3">
          <span className="font-medium text-ink">{isUser ? 'You' : agentName || 'Agent'}</span>
          <span>· {ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          {queued ? (
            <span data-testid="queued" className="rounded-full bg-accent-50 px-2 py-px font-medium text-accent-700">
              Queued: runs after the current turn
            </span>
          ) : null}
          {queued && onCancel ? (
            <button type="button" onClick={onCancel} data-testid="queued-cancel" className="rounded-full px-2 py-px font-medium text-ink hover:bg-line-soft">
              Cancel
            </button>
          ) : null}
          {cancelled ? (
            <span data-testid="never-ran" className="rounded-full bg-line-soft px-2 py-px text-ink-2">
              Cancelled: never ran
            </span>
          ) : null}
          <span className="flex-1" />
          {!isUser ? (
            <button
              type="button"
              aria-label="Copy"
              onClick={() => {
                void navigator.clipboard.writeText(message.content);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className="rounded-md p-1 text-ink-3 opacity-0 transition-opacity hover:bg-line-soft hover:text-ink focus-visible:opacity-100 group-hover/msg:opacity-100"
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
            </button>
          ) : null}
          {onFork ? (
            <button
              type="button"
              onClick={onFork}
              className="inline-flex items-center gap-1 rounded-md px-1 text-ink-3 opacity-0 transition-opacity hover:text-accent-700 focus-visible:opacity-100 group-hover/msg:opacity-100"
            >
              <GitBranch size={12} /> Fork from here
            </button>
          ) : null}
        </div>
        {message.content ? (
          <div className={cn('leading-relaxed', isUser ? 'w-fit max-w-full rounded-[10px] border border-line-soft bg-canvas px-3.5 py-2.5' : '', cancelled && 'text-ink-3 line-through')}>
            <MessageContent content={message.content} />
          </div>
        ) : null}
        {message.toolCalls?.length ? (
          <div className="flex w-full flex-col gap-2">
            {message.toolCalls.map((tool) => (
              <ToolCard key={tool.id} tool={tool} />
            ))}
          </div>
        ) : null}
      </div>
    </motion.article>
  );
}

function MessageContent({ content }: { content: string }): JSX.Element {
  const parts = content.split(/(```[\s\S]*?```)/g);
  return (
    <>
      {parts.map((part, index) => {
        const match = part.startsWith('```') ? part.match(/```(\w+)?\n?([\s\S]*?)```/) : null;
        if (match) {
          const [, lang, code] = match;
          return (
            <div key={index} className="my-3 overflow-hidden rounded-lg bg-ink">
              <div className="flex items-center justify-between border-b border-white/10 px-3.5 py-1.5 text-xs text-white/60">
                <span className="flex items-center gap-2">
                  <Code size={12} />
                  {lang || 'code'}
                </span>
                <button type="button" aria-label="Copy code" onClick={() => void navigator.clipboard.writeText(code.trim())} className="rounded p-1 hover:bg-white/10 hover:text-white">
                  <Copy size={12} />
                </button>
              </div>
              <pre className="overflow-x-auto px-3.5 py-3">
                <code className="font-mono text-[12.5px] leading-[1.7] text-canvas">{code.trim()}</code>
              </pre>
            </div>
          );
        }
        return (
          <span key={index} className="whitespace-pre-wrap break-words">
            {part}
          </span>
        );
      })}
    </>
  );
}

/** A line in the conversation for something that happened (an approval answered, a run stopped). */
export function EventNote({ children, icon }: { children: React.ReactNode; icon?: React.ReactNode }): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[10px] border border-dashed border-line-strong px-3 py-2 text-[13px] text-ink-2">
      {icon ?? <Warning size={15} aria-hidden="true" />}
      <span>{children}</span>
    </div>
  );
}
