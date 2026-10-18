/**
 * D12 §5 B / T14 + G6 — Subagents UI honesty: live BFF or unavailable banner.
 * Mock theater (`mockSubagents` / `mockSubagent` / `mockParentSessions`) fails.
 * Naming: provider `"memory"` must never be labeled long-term memory / LTM.
 * Echo path only as `lab_echo` badge (or honest unavailable) — unmarked Done forbidden.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');
const subagentsDir = join(root, 'app/subagents');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

function readSubagentsPage(rel: string): string {
  const path = join(subagentsDir, rel);
  expect(existsSync(path), `missing ${path}`).toBe(true);
  return readFileSync(path, 'utf8');
}

/** Live BFF client for list children / spawn (product base URL, not supervisor). */
function usesLiveBff(src: string): boolean {
  return (
    /sessionsAPI\.(listChildren|spawnSubagent|spawn|listSubagents)|subagentsAPI|useSubagents|useSpawnSubagent/.test(
      src
    ) ||
    /\/sessions\/\$\{[^}]+\}\/(children|subagents)/.test(src) ||
    /[`'"]\/sessions\/[^`'"]+\/(children|subagents)/.test(src)
  );
}

/** Honest unavailable / not-wired empty state (no silent success theater). */
function usesUnavailableHonesty(src: string): boolean {
  return /subagents-unavailable|SubagentsUnavailable|not wired|not available yet|subagents are not wired/i.test(
    src
  );
}

function isHonest(src: string): boolean {
  const hasMockTheater =
    /\bmockSubagents\b|\bmockSubagent\b|\bmockParentSessions\b/.test(src);
  if (hasMockTheater) return false;
  return usesLiveBff(src) || usesUnavailableHonesty(src);
}

function collectTsFiles(dir: string, out: string[] = []): string[] {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '.next' || name === 'd12_acceptance') {
      continue;
    }
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      collectTsFiles(full, out);
    } else if (/\.(ts|tsx)$/.test(name) && !name.endsWith('.d.ts')) {
      out.push(full);
    }
  }
  return out;
}

describe('D12 Subagents UI honesty (Acceptance B + G6)', () => {
  it('list page has no mockSubagents silent success theater', () => {
    const src = readSubagentsPage('page.tsx');
    expect(src).not.toMatch(/\bmockSubagents\b/);
    expect(src).not.toMatch(/\bconst\s+mockSubagents\b/);
    expect(isHonest(src)).toBe(true);
  });

  it('detail page has no mockSubagent theater', () => {
    const src = readSubagentsPage('[id]/page.tsx');
    expect(src).not.toMatch(/\bmockSubagent\b/);
    expect(isHonest(src)).toBe(true);
  });

  it('spawn page has no mockParentSessions theater', () => {
    const src = readSubagentsPage('spawn/page.tsx');
    expect(src).not.toMatch(/\bmockParentSessions\b/);
    expect(isHonest(src)).toBe(true);
  });

  it('each /subagents page is live BFF (children/spawn) OR honest unavailable banner', () => {
    for (const rel of ['page.tsx', '[id]/page.tsx', 'spawn/page.tsx']) {
      const src = readSubagentsPage(rel);
      const live = usesLiveBff(src);
      const unavailable = usesUnavailableHonesty(src);
      expect(
        live || unavailable,
        `${rel}: need sessions children/spawn via BFF or subagents-unavailable / not wired`
      ).toBe(true);
    }
  });

  it('naming trap: never label provider "memory" as long-term memory / LTM', () => {
    for (const rel of ['page.tsx', '[id]/page.tsx', 'spawn/page.tsx']) {
      const src = readSubagentsPage(rel);
      // Ban conflating echo stub provider name with D7 durable memory.
      expect(src).not.toMatch(/long[- ]term memory/i);
      expect(src).not.toMatch(/\bLTM\b/);
      expect(src).not.toMatch(
        /provider["'\s:=]+["']?memory["']?[^.\n]{0,80}(long[- ]term|LTM|durable memory)/i
      );
      expect(src).not.toMatch(
        /(long[- ]term|LTM|durable memory)[^.\n]{0,80}provider["'\s:=]+["']?memory/i
      );
    }
  });

  it('echo/lab path: lab_echo badge OR unavailable — no unmarked production-ready with mocks', () => {
    const pages = ['page.tsx', '[id]/page.tsx', 'spawn/page.tsx']
      .map((rel) => readSubagentsPage(rel))
      .join('\n');
    const hasMocks =
      /\bmockSubagents\b|\bmockSubagent\b|\bmockParentSessions\b/.test(pages);
    const hasLabEcho = /\blab_echo\b/.test(pages);
    const unavailable = usesUnavailableHonesty(pages);
    const liveClean = usesLiveBff(pages) && !hasMocks;

    // Unmarked Done while mock theater remains is forbidden.
    expect(pages).not.toMatch(/production subagents ready/i);
    expect(pages).not.toMatch(/subagents (are )?production[- ]ready/i);

    if (hasMocks) {
      expect.fail(
        'mock theater present — remove mocks; ship live BFF, lab_echo badge, or unavailable banner'
      );
    }

    expect(
      hasLabEcho || unavailable || liveClean,
      'need lab_echo badge, unavailable honesty, or live BFF without mocks'
    ).toBe(true);
  });

  it('product src never claims production subagents ready while mock fixtures remain anywhere under app/subagents', () => {
    const files = collectTsFiles(subagentsDir);
    const combined = files.map((f) => readFileSync(f, 'utf8')).join('\n');
    const hasMocks =
      /\bmockSubagents\b|\bmockSubagent\b|\bmockParentSessions\b/.test(combined);
    if (hasMocks) {
      expect(combined).not.toMatch(/production subagents ready/i);
      // Soft claim still fails G6 when mocks drive the UI.
      expect(combined).not.toMatch(/\bDone\b.*subagents|subagents.*\bDone\b/i);
    }
  });
});
