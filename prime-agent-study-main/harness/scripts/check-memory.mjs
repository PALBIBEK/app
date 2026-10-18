/**
 * Verify memory profile bind + extension exports.
 * Usage: node scripts/check-memory.mjs <workspace_dir>
 */
import { inspectMemoryBind } from "../dist/extensions/memory/index.js";

const cwd = process.argv[2];
if (!cwd) {
  console.error("usage: node scripts/check-memory.mjs <workspace_dir>");
  process.exit(1);
}

const result = inspectMemoryBind(cwd);
console.log(JSON.stringify(result));
