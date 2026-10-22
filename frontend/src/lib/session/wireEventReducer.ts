/**
 * Reduce supervisor WireEvents into chat UI state.
 */

import type { ChatMessage, ToolCallView, WireEvent } from '../api/types/session.types';
import { explainToolResult, readToolResult } from './explain';

export interface SubagentChipView {
  run_id: string;
  child_session_id: string;
  mode: string;
  provider: string;
  label?: string;
  status: 'running' | 'completed' | 'failed';
  stop_reason?: string;
}

export interface SessionViewState {
  messages: ChatMessage[];
  pendingTools: Map<string, ToolCallView>;
  pendingSubagents: Map<string, SubagentChipView>;
  pendingApprovals: Array<{
    approval_id: string;
    prompt: string;
    options: string[];
    timeout_s?: number;
    /** When it times out (epoch ms), for the countdown. */
    deadline_ms?: number;
  }>;
  lastSeq: number;
  settled: boolean;
  error: string | None;
  awaitingApproval: boolean;
  /** A run is open (between run_start and run_end). */
  running: boolean;
  /** Model steps taken in the current (or last) run. */
  runSteps: number;
  /** Tokens and cost over every turn seen. */
  usage: { input_tokens: number; output_tokens: number; cost: number };
  /** The session's (default) sandbox, from its hand events; null until one is built. */
  sandbox: SandboxView | null;
}

export interface SandboxView {
  status: 'ready' | 'stopped' | 'lost';
  /** Why it is ready: prewarm, first_use, resumed, replaced, adopted… */
  cause?: string;
  /** Stopped: how long it had been idle. */
  idle_s?: number;
}

type None = null;

export function createSessionViewState(): SessionViewState {
  return {
    messages: [],
    pendingTools: new Map(),
    pendingSubagents: new Map(),
    pendingApprovals: [],
    lastSeq: -1,
    settled: false,
    error: null,
    awaitingApproval: false,
    running: false,
    runSteps: 0,
    usage: { input_tokens: 0, output_tokens: 0, cost: 0 },
    sandbox: null,
  };
}

function textFromMessageEnd(data: Record<string, unknown>): string {
  const content = data.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (part && typeof part === 'object' && 'text' in part) {
          return String((part as { text: unknown }).text ?? '');
        }
        return '';
      })
      .join('');
  }
  return '';
}

/** Product label for echo stub — never "long-term memory". */
export function displaySubagentProvider(provider: string): {
  label: string;
  isLab: boolean;
} {
  const raw = (provider || '').trim();
  if (raw === 'memory' || raw === 'lab_echo') {
    return { label: 'lab_echo', isLab: true };
  }
  return { label: raw || 'unknown', isLab: false };
}

export function applyWireEvent(state: SessionViewState, event: WireEvent): SessionViewState {
  // A reconnect resumes after the last seq seen; anything at or before it is a replay.
  if (event.seq <= state.lastSeq) return state;
  const next: SessionViewState = {
    ...state,
    pendingTools: new Map(state.pendingTools),
    pendingSubagents: new Map(state.pendingSubagents),
    pendingApprovals: [...state.pendingApprovals],
    messages: [...state.messages],
    lastSeq: Math.max(state.lastSeq, event.seq),
  };

  switch (event.type) {
    case 'tool_start': {
      const id = String(event.data.tool_call_id ?? event.event_id);
      next.pendingTools.set(id, {
        id,
        name: String(event.data.tool_name ?? 'tool'),
        args: (event.data.args as Record<string, unknown>) ?? undefined,
        status: 'running',
      });
      break;
    }
    case 'tool_end': {
      const id = String(event.data.tool_call_id ?? event.event_id);
      const prev = next.pendingTools.get(id);
      const result = readToolResult(event.data.result ?? event.data.output, event.data.error);
      next.pendingTools.set(id, {
        id,
        name: prev?.name ?? String(event.data.tool_name ?? 'tool'),
        args: prev?.args ?? (event.data.args as Record<string, unknown> | undefined),
        output: result.ok ? result.payload : undefined,
        status: result.ok ? 'success' : 'error',
        error: result.ok ? undefined : result.error ?? 'failed',
        code: result.code,
        explanation: explainToolResult(result),
      });
      break;
    }
    case 'subagent_start': {
      const runId = String(event.data.run_id ?? event.event_id);
      const provider = String(event.data.provider ?? 'lab_echo');
      next.pendingSubagents.set(runId, {
        run_id: runId,
        child_session_id: String(
          event.data.child_session_id ?? event.data.child_id ?? ''
        ),
        mode: String(event.data.mode ?? 'spawn'),
        provider: displaySubagentProvider(provider).label,
        label:
          event.data.label != null ? String(event.data.label) : undefined,
        status: 'running',
      });
      break;
    }
    case 'subagent_end': {
      const runId = String(event.data.run_id ?? event.event_id);
      const prev = next.pendingSubagents.get(runId);
      const stop = String(event.data.stop_reason ?? 'completed');
      const failed = /fail|error|cancel|abort/i.test(stop);
      next.pendingSubagents.set(runId, {
        run_id: runId,
        child_session_id: prev?.child_session_id ?? '',
        mode: prev?.mode ?? 'spawn',
        provider: prev?.provider ?? 'lab_echo',
        label: prev?.label,
        status: failed ? 'failed' : 'completed',
        stop_reason: stop,
      });
      break;
    }
    case 'message_end': {
      const roleRaw = String(event.data.role ?? 'assistant');
      const role =
        roleRaw === 'user' || roleRaw === 'system' ? roleRaw : 'assistant';
      const content = textFromMessageEnd(event.data);
      const timestamp = new Date(event.ts_ms).toISOString();
      if (role === 'assistant') {
        next.runSteps += 1;
        const usage = event.data.usage as Record<string, unknown> | null | undefined;
        if (usage) {
          next.usage = {
            input_tokens: next.usage.input_tokens + Number(usage.input_tokens ?? 0),
            output_tokens: next.usage.output_tokens + Number(usage.output_tokens ?? 0),
            cost: next.usage.cost + Number(usage.cost_total ?? 0),
          };
        }
      }
      if (role === 'user') {
        // The journal's copy of a message this page just showed: confirm it in place.
        const local = next.messages.findIndex((m) => m.id.startsWith('local-') && m.role === 'user' && m.content === content);
        if (local >= 0) {
          next.messages = next.messages.map((m, i) => (i === local ? { ...m, id: event.event_id, timestamp, seq: event.seq } : m));
          break;
        }
      }
      const tools = Array.from(next.pendingTools.values());
      if (role === 'assistant' && !content && !tools.length) {
        break; // only tool calls: they show as tools as they run, not as an empty bubble
      }
      next.pendingTools.clear();
      next.messages.push({
        id: event.event_id,
        role,
        content,
        timestamp,
        toolCalls: tools.length ? tools : undefined,
        seq: event.seq,
      });
      break;
    }
    case 'approval_required': {
      const approvalId = String(event.data.approval_id ?? event.event_id);
      const prompt = String(event.data.prompt ?? 'Approval required');
      const options = Array.isArray(event.data.options)
        ? event.data.options.map((o) => String(o))
        : ['approve', 'deny'];
      const timeout = typeof event.data.timeout_s === 'number' ? event.data.timeout_s : undefined;
      next.pendingApprovals = [
        ...next.pendingApprovals.filter((a) => a.approval_id !== approvalId),
        {
          approval_id: approvalId,
          prompt,
          options,
          timeout_s: timeout,
          deadline_ms: timeout != null ? event.ts_ms + timeout * 1000 : undefined,
        },
      ];
      next.awaitingApproval = true;
      break;
    }
    case 'approval_resolved': {
      const approvalId = String(event.data.approval_id ?? '');
      next.pendingApprovals = next.pendingApprovals.filter(
        (a) => a.approval_id !== approvalId
      );
      next.awaitingApproval = next.pendingApprovals.length > 0;
      break;
    }
    case 'error': {
      next.error = String(event.data.message ?? event.data.error ?? 'Session error');
      break;
    }
    case 'hand_provisioned':
    case 'hand_stopped':
    case 'hand_lost': {
      if ((event.data.hand ?? 'default') !== 'default') break;
      if (event.type === 'hand_provisioned') next.sandbox = { status: 'ready', cause: String(event.data.cause ?? '') };
      else if (event.type === 'hand_stopped') next.sandbox = { status: 'stopped', idle_s: Number(event.data.idle_s ?? 0) };
      else next.sandbox = { status: 'lost' };
      break;
    }
    case 'run_start': {
      next.running = true;
      next.runSteps = 0;
      next.settled = false;
      next.error = null;
      break;
    }
    case 'settled':
    case 'run_end': {
      next.settled = true;
      next.running = false;
      next.pendingApprovals = [];
      next.awaitingApproval = false;
      break;
    }
    default:
      break;
  }
  return next;
}
