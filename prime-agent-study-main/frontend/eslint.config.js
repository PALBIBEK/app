import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import boundaries from "eslint-plugin-boundaries";
import tseslint from "typescript-eslint";

/** @type {import('eslint').Linter.Config[]} */
export default tseslint.config(
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/*.tsbuildinfo",
      "prototype.html",
      "pnpm-lock.yaml",
      "packages/tokens/style-dictionary.config.mjs",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  prettier,
  {
    plugins: {
      boundaries,
    },
    settings: {
      "boundaries/include": ["packages/**/*.{js,jsx,ts,tsx}"],
      "boundaries/elements": [
        { type: "tokens", pattern: "packages/tokens/*" },
        { type: "core", pattern: "packages/core/*" },
        { type: "ui", pattern: "packages/ui/*" },
        {
          type: "features-own",
          pattern: "packages/features/src/*",
          capture: ["feature"],
          mode: "folder",
        },
        { type: "features", pattern: "packages/features/*" },
        { type: "app", pattern: "packages/app-web/*" },
      ],
    },
    rules: {
      "boundaries/element-types": [
        "error",
        {
          default: "disallow",
          rules: [
            { from: "tokens", allow: [] },
            { from: "core", allow: ["core", "tokens"] },
            { from: "ui", allow: ["ui", "tokens", "core"] },
            {
              from: ["features", "features-own"],
              allow: ["features-own", "ui", "core", "tokens"],
            },
            { from: "app", allow: ["*"] },
          ],
        },
      ],
    },
  },
  {
    files: ["packages/core/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "react",
              message: "Core must stay rendering-free (no React).",
            },
            {
              name: "react-dom",
              message: "Core must stay rendering-free (no React DOM).",
            },
            {
              name: "@yal/ui",
              message: "Core must not import @yal/ui.",
            },
          ],
          patterns: [
            {
              group: ["react/*", "react-dom/*", "@yal/ui/*", "*.css", "**/*.css"],
              message: "Core must stay rendering-free (no React / CSS).",
            },
          ],
        },
      ],
    },
  },
);
