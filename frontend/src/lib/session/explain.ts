/**
 * Tool results in plain words. The supervisor answers a refused or failed call with a
 * code (`result.code`); the chat says what happened and what the user can do.
 */

const EXPLAINED: Record<string, string> = {
  STEP_LIMIT: 'The agent used all its model steps for this run, so the call was not made. Raise the step limit on the Policy tab, or ask it to continue.',
  TRUNCATED_TOOL_CALL: 'The model’s call was cut off before it finished, so it was not run. The agent was asked to send it again.',
  MALFORMED_ARGUMENTS: 'The model sent arguments that were not valid JSON, so the call was not run. The agent was asked to send it again.',
  APPROVAL_DENIED: 'You (or the approval timeout) denied this call, so it did not run.',
  FILE_EFFECTS_DENIED: 'The sandbox does not allow this: the harness’s Sandbox tab limits what the agent may change.',
  PATH_ESCAPE: 'The path is outside the agent’s workspace, so the call was refused.',
  FILE_NOT_FOUND: 'That file does not exist in the workspace.',
  TOOL_TIMEOUT: 'The tool took too long and was stopped.',
  TOOL_NOT_FOUND: 'The harness does not have this tool.',
  TOOL_NOT_IMPLEMENTED: 'The supervisor cannot run this tool.',
  TOOL_OUTCOME_UNKNOWN: 'The call was interrupted; whether it took effect is unknown, so it was not repeated. The agent was told and carried on.',
  TOOL_NOT_RUN: 'The run stopped before this call started, so it did not run.',
  WORKSPACE_UNAVAILABLE: 'The agent’s workspace is not available right now.',
  BAD_ARGS: 'The tool was called with arguments it does not accept.',
};

export interface ToolResultView {
  ok: boolean;
  payload?: unknown;
  error?: string;
  code?: string;
}

/** The `{ok, payload, error, code}` a tool answers with, from whatever shape arrived. */
export function readToolResult(result: unknown, error?: unknown): ToolResultView {
  if (result && typeof result === 'object' && 'ok' in (result as object)) {
    const r = result as Record<string, unknown>;
    return {
      ok: r.ok === true,
      payload: r.payload,
      error: typeof r.error === 'string' ? r.error : undefined,
      code: typeof r.code === 'string' ? r.code : undefined,
    };
  }
  if (error) return { ok: false, error: String(error) };
  return { ok: true, payload: result };
}

/** What to tell the user about a failed call: the explanation for its code, else its own error. */
export function explainToolResult(view: ToolResultView): string | undefined {
  if (view.ok) return undefined;
  if (view.code && EXPLAINED[view.code]) return EXPLAINED[view.code];
  if (view.error && /blocked by sandbox profile/.test(view.error)) {
    return 'The sandbox blocked this call. Shell commands on a host-process sandbox need “Everything the sandbox can reach” on the Sandbox tab, or an isolating sandbox (Docker, E2B).';
  }
  return view.error ?? 'The call failed.';
}

/** Long output, shortened for the chat: start and end kept, the middle counted. */
export function clip(text: string, max = 1200): string {
  if (text.length <= max) return text;
  const half = Math.floor(max / 2);
  return `${text.slice(0, half)}\n… ${text.length - max} more characters …\n${text.slice(-half)}`;
}
