/**
 * Phase 1 foundation acceptance (red → green → refactor).
 * Run: node --test scripts/phase1-foundation.test.mjs
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
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
    } else {
      out.push(p);
    }
  }
  return out;
}

function read(rel) {
  return readFileSync(join(ROOT, rel), "utf8");
}

describe("Phase 1 — tokens", () => {
  it("emits CSS, TS, and JSON from Style Dictionary build", () => {
    for (const f of [
      "packages/tokens/dist/tokens.css",
      "packages/tokens/dist/tokens.ts",
      "packages/tokens/dist/tokens.json",
    ]) {
      assert.ok(existsSync(join(ROOT, f)), `missing ${f} — run pnpm --filter @yal/tokens build`);
    }
  });

  it("CSS defines semantic tokens and dark theme overrides", () => {
    const css = read("packages/tokens/dist/tokens.css");
    assert.match(css, /--color-accent-default/);
    assert.match(css, /--color-status-success/);
    assert.match(css, /\[data-theme=["']dark["']\]|:root\[data-theme=dark\]|\[data-theme=dark\]/);
    assert.match(css, /--density-row-height/);
    assert.match(css, /\[data-density=["']compact["']\]|\[data-density=compact\]/);
  });

  it("source is two-tier (primitive + semantic), not hand-copied CSS only", () => {
    assert.ok(existsSync(join(ROOT, "packages/tokens/src/primitive")));
    assert.ok(existsSync(join(ROOT, "packages/tokens/src/semantic")));
    assert.ok(existsSync(join(ROOT, "packages/tokens/style-dictionary.config.mjs")));
  });
});

describe("Phase 1 — boundaries & tooling", () => {
  it("has ESLint flat config with boundaries + core no-react ban", () => {
    assert.ok(existsSync(join(ROOT, "eslint.config.js")));
    const cfg = read("eslint.config.js");
    assert.match(cfg, /boundaries/);
    assert.match(cfg, /no-restricted-imports/);
    assert.match(cfg, /packages\/core/);
  });

  it("root tsconfig enables maximal strictness flags", () => {
    const base = read("tsconfig.base.json");
    assert.match(base, /"strict":\s*true/);
    assert.match(base, /noUncheckedIndexedAccess/);
    assert.match(base, /exactOptionalPropertyTypes/);
    assert.match(base, /noImplicitOverride/);
  });

  it("core source has zero react / css imports", () => {
    const files = walk(join(ROOT, "packages/core/src")).filter((f) =>
      /\.(ts|tsx)$/.test(f),
    );
    assert.ok(files.length > 0, "core must have source files");
    const banned =
      /\bfrom\s+["'](react|react-dom|react\/|@yal\/ui|@yal\/features|@yal\/app-web)["']|\.css["']/;
    for (const file of files) {
      assert.equal(
        banned.test(readFileSync(file, "utf8")),
        false,
        `forbidden import in ${relative(ROOT, file)}`,
      );
    }
  });
});

describe("Phase 1 — core skeleton", () => {
  it("exports transport, AppError, can, i18n, and format helpers", () => {
    const index = read("packages/core/src/index.ts");
    for (const sym of [
      "request",
      "AppError",
      "can",
      "t",
      "formatTimestamp",
      "formatDuration",
      "formatCost",
      "formatTokens",
      "flags",
    ]) {
      assert.match(index, new RegExp(sym));
    }
  });

  it("AppError includes budgetExceeded", () => {
    const files = walk(join(ROOT, "packages/core/src")).map((f) =>
      readFileSync(f, "utf8"),
    );
    const blob = files.join("\n");
    assert.match(blob, /budgetExceeded/);
  });
});

describe("Phase 1 — primitives", () => {
  const shipped = [
    "Button",
    "IconButton",
    "Input",
    "Textarea",
    "Checkbox",
    "Radio",
    "Switch",
    "Select",
    "Badge",
    "Spinner",
    "Skeleton",
    "EmptyState",
    "ErrorState",
    "Tooltip",
    "Dialog",
    "Menu",
    "Tabs",
    "Toast",
  ];

  it("ships required primitives from @yal/ui", () => {
    const index = read("packages/ui/src/index.ts");
    for (const name of shipped) {
      assert.match(index, new RegExp(name), `missing export ${name}`);
    }
  });

  it("four-state primitives exist as dedicated modules", () => {
    for (const name of ["Skeleton", "EmptyState", "ErrorState", "Spinner"]) {
      const hits = walk(join(ROOT, "packages/ui/src")).filter((f) =>
        f.includes(name),
      );
      assert.ok(hits.length > 0, `missing module for ${name}`);
    }
  });

  it("EmptyState documents no-data vs no-match variants", () => {
    const hits = walk(join(ROOT, "packages/ui/src")).filter((f) =>
      /EmptyState/.test(f),
    );
    assert.ok(hits.length > 0);
    const blob = hits.map((f) => readFileSync(f, "utf8")).join("\n");
    assert.match(blob, /no-data|noData|no_data/);
    assert.match(blob, /no-match|noMatch|no_match/);
  });
});

describe("Phase 1 — shell & demos", () => {
  it("app-web has tenant route structure and kitchen-sink", () => {
    const files = walk(join(ROOT, "packages/app-web/src"))
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    assert.match(files, /\$tenant|tenant/);
    assert.match(files, /kitchen-sink|kitchenSink/);
    assert.match(files, /data-theme|theme/);
    assert.match(files, /CommandPalette|command-palette|⌘|Meta\+k|mod\+k/i);
  });

  it("Ladle config exists for component stories", () => {
    assert.ok(
      existsSync(join(ROOT, "packages/ui/.ladle")) ||
        existsSync(join(ROOT, "packages/ui/ladle.config.mjs")) ||
        existsSync(join(ROOT, ".ladle/config.mjs")) ||
        walk(join(ROOT, "packages/ui")).some((f) => f.endsWith(".stories.tsx")),
      "expected Ladle config or *.stories.tsx under ui",
    );
  });
});
