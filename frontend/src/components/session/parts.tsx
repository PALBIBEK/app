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
  CheckCircle,
  CircleNotch,
  Code,
  Copy,
  GitBranch,
  Robot,
  ShieldWarning,
  User,
  Warning,
} from '@phosphor-icons/react';

import type { ChatMessage, ToolCallView } from '@/lib/api/types/session.types';
import { clip } from '@/lib/session/explain';
import { displaySubagentProvider, type SubagentChipView } from '@/lib/session/wireEventReducer';
import { cn } from '@/lib/utils';

const messageVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.25 } },
  exit: { opacity: 0, transition: { duration: 0.15 } },
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

/** A tool call: what it was asked to do, what came back, and a refusal in plain words. */
export function ToolCard({ tool }: { tool: ToolCallView }): JSX.Element {
  const [open, setOpen] = useState(false);
  const args = tool.args && Object.keys(tool.args).length ? JSON.stringify(tool.args) : '';
  const output = show(tool.output);
  return (
    <div data-testid="tool-card" className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm shadow-sm">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 text-left">
        <span
          className={cn(
            'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg',
            tool.status === 'success' && 'bg-secondary-100',
            tool.status === 'running' && 'bg-primary-100',
            tool.status === 'error' && 'bg-red-100'
          )}
        >
          {tool.status === 'success' && <CheckCircle size={15} weight="fill" className="text-secondary-600" />}
          {tool.status === 'running' && <CircleNotch size={15} className="animate-spin text-primary-600" />}
          {tool.status === 'error' && <Warning size={15} weight="fill" className="text-red-600" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="font-mono font-medium text-slate-800">{tool.name}</span>
          {args ? <span className="ml-2 truncate font-mono text-xs text-slate-500">{args.length > 80 ? `${args.slice(0, 80)}…` : args}</span> : null}
        </span>
        {open ? <CaretDown size={14} className="text-slate-400" /> : <CaretRight size={14} className="text-slate-400" />}
      </button>
      {tool.status === 'error' ? (
        <p data-testid="tool-explanation" className="mt-2 text-xs text-red-700">
          {tool.explanation ?? tool.error}
          {tool.code ? <span className="ml-1 font-mono text-[10px] text-red-400">{tool.code}</span> : null}
        </p>
      ) : null}
      {open ? (
        <div className="mt-3 space-y-2">
          {args ? <pre className="overflow-x-auto rounded-lg bg-white p-2 font-mono text-[11px] text-slate-700">{JSON.stringify(tool.args, null, 2)}</pre> : null}
          {output ? <pre data-testid="tool-output" className="max-h-80 overflow-auto rounded-lg bg-slate-900 p-2 font-mono text-[11px] text-slate-100">{clip(output)}</pre> : null}
        </div>
      ) : output && tool.status === 'success' ? (
        <p data-testid="tool-output" className="mt-1.5 truncate font-mono text-xs text-slate-500">{output.split('\n')[0]}</p>
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
    <div className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50/80 px-4 py-3 shadow-sm">
      <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-100">
        {chip.status === 'running' ? <CircleNotch size={15} className="animate-spin text-primary-600" /> : <GitBranch size={15} weight="fill" className="text-amber-700" />}
      </span>
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium text-slate-800">
          Subagent {chip.status}
          {chip.label ? ` · ${chip.label}` : ''}
        </p>
        <p className="text-xs text-slate-500">
          {chip.mode.replace(/_/g, ' ')} · {provider.label}
          {chip.stop_reason ? ` · ${chip.stop_reason}` : ''}
        </p>
      </div>
      <Link href={href} className="text-xs font-medium text-primary-600 hover:text-primary-700">
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
  const allow = options.find((o) => /^(approve|allow|yes|y|ok)$/i.test(o)) ?? options[0];
  const deny = options.find((o) => /^(deny|reject|no|n)$/i.test(o)) ?? options[options.length - 1];
  return (
    <div data-testid="approval-banner" className="flex flex-col gap-3 rounded-xl border border-amber-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <ShieldWarning size={22} weight="fill" className="mt-0.5 text-amber-600" />
        <div>
          <p className="text-sm font-semibold text-slate-900">Approval required</p>
          <p className="mt-1 text-sm text-slate-600">{prompt}</p>
          {left != null ? (
            <p data-testid="approval-countdown" className="mt-1 text-xs text-amber-700">
              {left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left` : 'Timing out'}
              {onTimeout ? ` · then it is ${onTimeout === 'allow' ? 'allowed' : 'denied'}` : ''}
            </p>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button type="button" data-testid="approval-deny" onClick={() => onAnswer(deny)} className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
          {deny}
        </button>
        <button type="button" data-testid="approval-approve" onClick={() => onAnswer(allow)} className="rounded-xl bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700">
          {allow}
        </button>
      </div>
    </div>
  );
}

export function MessageBubble({
  message,
  queued,
  cancelled,
  onCancel,
  onFork,
}: {
  message: ChatMessage;
  queued?: boolean;
  /** Its run was cancelled before it started: the message never ran. */
  cancelled?: boolean;
  /** Cancel a queued message's run. */
  onCancel?: () => void;
  onFork?: () => void;
}): JSX.Element {
  const isUser = message.role === 'user';
  const [copied, setCopied] = useState(false);
  const ts = new Date(message.timestamp);
  return (
    <motion.div variants={messageVariants} initial="hidden" animate="visible" exit="exit" className={cn('group/msg flex items-start gap-4', isUser && 'flex-row-reverse')}>
      <div className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl shadow-md', isUser ? 'bg-gradient-to-br from-slate-700 to-slate-800' : 'bg-gradient-to-br from-primary-500 to-primary-600')}>
        {isUser ? <User size={18} weight="fill" className="text-white" /> : <Robot size={18} weight="fill" className="text-white" />}
      </div>
      <div className={cn('min-w-0 flex-1 space-y-3', isUser && 'flex flex-col items-end')}>
        <div className={cn('group relative rounded-2xl px-5 py-4', isUser ? 'max-w-[85%] bg-gradient-to-br from-primary-500 to-primary-600 text-white shadow-lg shadow-primary-500/20' : 'max-w-full border border-slate-200 bg-white shadow-md')}>
          {!isUser && (
            <button
              type="button"
              aria-label="Copy"
              onClick={() => {
                void navigator.clipboard.writeText(message.content);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
              className="absolute right-3 top-3 rounded-lg p-1.5 text-slate-400 opacity-0 transition-all hover:bg-slate-100 group-hover:opacity-100"
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
            </button>
          )}
          <div className={cn('text-[15px] leading-relaxed', !isUser && 'text-slate-700')}>
            <MessageContent content={message.content} />
          </div>
        </div>
        {message.toolCalls?.length ? (
          <div className="w-full space-y-2">
            {message.toolCalls.map((tool) => (
              <ToolCard key={tool.id} tool={tool} />
            ))}
          </div>
        ) : null}
        <p className={cn('text-xs text-slate-400', isUser && 'text-right')}>
          {queued ? <span data-testid="queued" className="mr-2 rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">Queued: runs after the current turn</span> : null}
          {queued && onCancel ? (
            <button type="button" onClick={onCancel} data-testid="queued-cancel" className="mr-2 rounded-full px-2 py-0.5 text-red-600 hover:bg-red-50">
              Cancel
            </button>
          ) : null}
          {cancelled ? <span data-testid="never-ran" className="mr-2 rounded-full bg-slate-100 px-2 py-0.5 text-slate-500">Cancelled: never ran</span> : null}
          {ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          {onFork ? (
            <button type="button" onClick={onFork} className="ml-3 inline-flex items-center gap-1 text-slate-400 opacity-0 transition-opacity hover:text-primary-600 group-hover/msg:opacity-100">
              <GitBranch size={11} /> Fork from here
            </button>
          ) : null}
        </p>
      </div>
    </motion.div>
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
            <div key={index} className="my-4 overflow-hidden rounded-xl bg-slate-900 shadow-lg">
              <div className="flex items-center justify-between border-b border-slate-700 px-4 py-2 text-xs text-slate-400">
                <span className="flex items-center gap-2"><Code size={12} />{lang || 'code'}</span>
                <button type="button" aria-label="Copy code" onClick={() => void navigator.clipboard.writeText(code.trim())} className="rounded p-1 hover:bg-slate-800 hover:text-slate-200">
                  <Copy size={12} />
                </button>
              </div>
              <pre className="overflow-x-auto p-4"><code className="font-mono text-sm leading-relaxed text-slate-100">{code.trim()}</code></pre>
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
