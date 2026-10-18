/**
 * Structured summarization via model gateway (OpenAI-compat chat completions).
 */
import { gatewayOpenAiBaseUrl, loadPlugBindings } from "../gateway.js";
import { loadMemoryProfile } from "./profile.js";

const SUMMARY_MARKER = "[context checkpoint]";

const SUMMARIZATION_SYSTEM_PROMPT =
  "You are a context summarization assistant. Your task is to read a " +
  "conversation between a user and an AI assistant, then produce a structured " +
  "summary following the exact format specified.\n\n" +
  "Do NOT continue the conversation. Do NOT respond to any questions in the " +
  "conversation. ONLY output the structured summary.";

const SUMMARIZATION_PROMPT =
  "The messages above are a conversation to summarize. Create a structured " +
  "context checkpoint that another LLM will use to continue the work.\n\n" +
  "Use sections: ## Goal, ## Progress, ## Next Steps, ## Critical Context.";

function messageContent(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  if (content === null || content === undefined) {
    return "";
  }
  return JSON.stringify(content);
}

function buildSummaryRequest(
  messages: Array<Record<string, unknown>>,
  previousSummary?: string | null,
): Array<{ role: string; content: string }> {
  const request: Array<{ role: string; content: string }> = [
    { role: "system", content: SUMMARIZATION_SYSTEM_PROMPT },
    ...messages.map((m) => ({
      role: String(m.role ?? "user"),
      content: messageContent(m.content),
    })),
  ];
  if (previousSummary) {
    request.push({
      role: "user",
      content:
        `<previous-summary>\n${previousSummary}\n</previous-summary>\n\n` +
        "Update the existing summary with the new messages. Preserve file paths and errors.",
    });
  } else {
    request.push({ role: "user", content: SUMMARIZATION_PROMPT });
  }
  return request;
}

export async function summarizeViaGateway(
  messages: Array<Record<string, unknown>>,
  cwd: string = process.cwd(),
  previousSummary?: string | null,
): Promise<string> {
  const base = gatewayOpenAiBaseUrl(cwd);
  if (!base) {
    throw new Error("missing gateway_base_url in .yal/plug_bindings.json for compaction summarization");
  }
  const profile = loadMemoryProfile(cwd);
  const plug = loadPlugBindings(cwd);
  const model =
    profile?.compaction.summarization_model ?? plug.model_route ?? "cheap-summary";
  const url = `${base}/chat/completions`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      messages: buildSummaryRequest(messages, previousSummary),
      tools: [],
    }),
  });
  if (!res.ok) {
    throw new Error(`gateway summarization failed: ${res.status} ${res.statusText}`);
  }
  const body = (await res.json()) as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  const text = body.choices?.[0]?.message?.content?.trim() ?? "";
  if (!text) {
    throw new Error("gateway summarization returned empty content");
  }
  return `${SUMMARY_MARKER}\n${text}`;
}

export { SUMMARY_MARKER };
