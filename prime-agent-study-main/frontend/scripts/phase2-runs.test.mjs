/**
 * Phase 2 — Runs vertical acceptance (red → green → refactor).
 * Run: node --test scripts/phase2-runs.test.mjs
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "node_modules" || name === "dist") continue;
      walk(p, out);
    } else out.push(p);
  }
  return out;
}

function blob(globsUnder) {
  return walk(join(ROOT, globsUnder))
    .filter((f) => /\.(ts|tsx|mjs|js)$/.test(f))
    .map((f) => readFileSync(f, "utf8"))
    .join("\n");
}

describe("Phase 2 — Runs vertical", () => {
  it("has runs/sessions search schema and query keys in core", () => {
    const core = blob("packages/core/src");
    assert.match(core, /runsSearchSchema|sessionsSearchSchema|searchSchema/);
    assert.match(core, /selected/);
    assert.match(core, /sort/);
    assert.match(core, /RUNS_CLIENT_SIDE_FILTER|CLIENT_SIDE_FILTER/);
  });

  it("maps journal event types including unknown fallback", () => {
    const core = blob("packages/core/src");
    for (const t of [
      "tool_start",
      "tool_end",
      "message_end",
      "compaction_end",
      "approval_request",
      "subagent_start",
      "Unknown",
    ]) {
      assert.match(core, new RegExp(t));
    }
  });

  it("counts unpaired tools", () => {
    const core = blob("packages/core/src");
    assert.match(core, /unpaired/i);
  });

  it("ships DataTable composite in ui", () => {
    const ui = blob("packages/ui/src");
    assert.match(ui, /DataTable/);
    assert.match(ui, /virtual/i);
    assert.match(readFileSync(join(ROOT, "packages/ui/src/index.ts"), "utf8"), /DataTable/);
  });

  it("ships runs feature list, detail, journal, launch", () => {
    const feat = blob("packages/features/src");
    assert.match(feat, /RunsList|RunsPage|runs\/list/i);
    assert.match(feat, /JournalTimeline|journal/i);
    assert.match(feat, /LaunchRun|launch/i);
    assert.match(feat, /DetailPane|RunDetail/i);
  });

  it("app-web wires runs routes with search + seq deep-link", () => {
    const app = blob("packages/app-web/src");
    assert.match(app, /runs\/\$/);
    assert.match(app, /seq/);
    assert.match(app, /validateSearch|runsSearch/);
    assert.match(app, /kitchen-sink|RunsList|features/);
  });

  it("prototype.html is retired", () => {
    assert.equal(
      existsSync(join(ROOT, "prototype.html")),
      false,
      "prototype.html should be deleted at Phase 2 exit",
    );
  });

  it("mutations send Idempotency-Key", () => {
    const core = blob("packages/core/src");
    assert.match(core, /Idempotency-Key/);
  });
});
