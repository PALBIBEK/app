/**
 * Phase 0 acceptance tests (red → green → refactor).
 * Run: node --test scripts/phase0-scaffold.test.mjs
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PACKAGES = ["tokens", "core", "ui", "features", "app-web"];

/** Lower packages must not depend on higher ones (design.md L1←L5). */
const FORBIDDEN_DEPS = {
  tokens: ["@yal/core", "@yal/ui", "@yal/features", "@yal/app-web"],
  core: ["@yal/ui", "@yal/features", "@yal/app-web", "react", "react-dom", "react-dom/client"],
  ui: ["@yal/features", "@yal/app-web"],
  features: ["@yal/app-web"],
  "app-web": [],
};

function pkgJson(name) {
  return JSON.parse(readFileSync(join(ROOT, "packages", name, "package.json"), "utf8"));
}

function walkTsFiles(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "node_modules" || name === "dist") continue;
      walkTsFiles(p, out);
    } else if (/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

describe("Phase 0 monorepo scaffold", () => {
  it("pins Node 22 via .nvmrc and root engines", () => {
    const nvmrc = readFileSync(join(ROOT, ".nvmrc"), "utf8").trim();
    assert.match(nvmrc, /^22(\.|$)/, `.nvmrc should pin Node 22, got ${nvmrc}`);
    const root = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
    assert.ok(root.engines?.node, "root package.json needs engines.node");
    assert.match(String(root.engines.node), /22/, "engines.node must require 22");
    assert.equal(root.packageManager?.startsWith("pnpm@"), true, "packageManager must pin pnpm");
  });

  it("declares pnpm workspaces for packages/*", () => {
    const ws = readFileSync(join(ROOT, "pnpm-workspace.yaml"), "utf8");
    assert.match(ws, /packages\/\*/);
  });

  it("has the five empty-layer packages", () => {
    for (const name of PACKAGES) {
      const dir = join(ROOT, "packages", name);
      assert.ok(existsSync(dir), `missing packages/${name}`);
      assert.ok(existsSync(join(dir, "package.json")), `missing packages/${name}/package.json`);
      assert.ok(existsSync(join(dir, "src", "index.ts")), `missing packages/${name}/src/index.ts`);
      assert.ok(existsSync(join(dir, "tsconfig.json")), `missing packages/${name}/tsconfig.json`);
    }
  });

  it("enforces layer dependency direction in package.json", () => {
    for (const name of PACKAGES) {
      const pkg = pkgJson(name);
      const deps = {
        ...pkg.dependencies,
        ...pkg.devDependencies,
        ...pkg.peerDependencies,
      };
      for (const forbidden of FORBIDDEN_DEPS[name]) {
        assert.equal(
          deps[forbidden],
          undefined,
          `${name} must not depend on ${forbidden}`,
        );
      }
    }
    assert.equal(pkgJson("tokens").name, "@yal/tokens");
    assert.equal(pkgJson("core").name, "@yal/core");
    assert.equal(pkgJson("ui").name, "@yal/ui");
    assert.equal(pkgJson("features").name, "@yal/features");
    assert.equal(pkgJson("app-web").name, "@yal/app-web");
  });

  it("keeps core free of rendering imports in source", () => {
    const files = walkTsFiles(join(ROOT, "packages", "core", "src"));
    const banned = /\bfrom\s+["'](react|react-dom|react-native|@yal\/ui|@yal\/features|@yal\/app-web)["']/;
    for (const file of files) {
      const src = readFileSync(file, "utf8");
      assert.equal(
        banned.test(src),
        false,
        `core rendering/upward import in ${relative(ROOT, file)}`,
      );
    }
  });

  it("keeps features free of product verticals until Phase 2", () => {
    const featureSrc = walkTsFiles(join(ROOT, "packages", "features", "src"));
    assert.equal(
      featureSrc.length,
      1,
      "features should only export an empty index until Phase 2",
    );
  });

  it("allows shell routes in app-web after Phase 1 (no feature modules yet)", () => {
    const featureImports = walkTsFiles(join(ROOT, "packages", "app-web", "src"))
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    assert.equal(
      /from\s+["']@yal\/features\//.test(featureImports),
      false,
      "app-web must not import feature modules until Phase 2",
    );
  });

  it("marks prototype.html as deprecated IA reference", () => {
    const html = readFileSync(join(ROOT, "prototype.html"), "utf8");
    assert.match(
      html,
      /DEPRECATED\s*[—-]\s*IA reference only/i,
      "prototype.html needs DEPRECATED — IA reference only header",
    );
  });

  it("documents OpenAPI contracts generation", () => {
    const readme = readFileSync(join(ROOT, "contracts", "README.md"), "utf8");
    assert.match(readme, /OpenAPI/i);
    assert.match(readme, /openapi\.json/i);
    assert.match(readme, /generat/i);
  });

  it("configures same-origin Vite proxy for /api", () => {
    const vitePath = join(ROOT, "packages", "app-web", "vite.config.ts");
    assert.ok(existsSync(vitePath), "app-web vite.config.ts required");
    const vite = readFileSync(vitePath, "utf8");
    assert.match(vite, /["']\/api/);
    assert.match(vite, /proxy/);
    assert.match(vite, /8080|target/);
  });

  it("documents same-origin API strategy in README", () => {
    const readme = readFileSync(join(ROOT, "README.md"), "utf8");
    assert.match(readme, /\/api\/v1/);
    assert.match(readme, /proxy/i);
    assert.match(readme, /same-origin|same origin/i);
  });
});
