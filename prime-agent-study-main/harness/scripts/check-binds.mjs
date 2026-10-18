/**
 * Verify compiled harness extensions read supervisor-materialized `.yal/`.
 * Usage: node scripts/check-binds.mjs <workspace_dir>
 */
import { registerPrompt } from "../dist/extensions/prompt.js";
import { registerTools } from "../dist/extensions/tools/index.js";

const cwd = process.argv[2];
if (!cwd) {
  console.error("usage: node scripts/check-binds.mjs <workspace_dir>");
  process.exit(1);
}

const prompt = registerPrompt(cwd);
const tools = registerTools(cwd);
console.log(
  JSON.stringify({
    ok: true,
    prompt_bytes: Buffer.byteLength(prompt, "utf8"),
    tool_count: tools.length,
    tool_names: tools.map((t) => t.name),
  }),
);
