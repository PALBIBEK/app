/**
 * D7 §5 D / T14 — FE /memory must be live BFF or explicitly not-theater.
 *
 * Theater = hardcoded mockMemories / mock stores rendered as if live.
 * Honest alternatives:
 *   - useMemory / memoryAPI live hooks
 *   - MemoryUnavailablePage (or inline) with harness 422 message
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const memoryDir = resolve(__dirname, '../app/memory');
const unavailableComponent = resolve(
  __dirname,
  '../components/memory/MemoryUnavailable.tsx',
);

function readPage(rel: string): string {
  const path = resolve(memoryDir, rel);
  expect(existsSync(path), `missing ${path}`).toBe(true);
  return readFileSync(path, 'utf8');
}

function isHonest(src: string): boolean {
  const usesLive = /useMemory|useMemoryEntry|useCreateMemory|memoryAPI/.test(src);
  const usesUnavailableComponent = /MemoryUnavailablePage/.test(src);
  const disabledHonesty =
    /not available yet|not wired|Memory stores are not available/i.test(src) &&
    !/\bmockMemories\b/.test(src);
  return usesLive || usesUnavailableComponent || disabledHonesty;
}

describe('D7 FE /memory honesty (T14)', () => {
  it('MemoryUnavailable (if used) carries harness 422 message', () => {
    if (!existsSync(unavailableComponent)) {
      return;
    }
    const src = readFileSync(unavailableComponent, 'utf8');
    expect(src).toMatch(/Memory stores are not available yet/);
    expect(src).not.toMatch(/\bmockMemories\b/);
  });

  it('page.tsx is live BFF or explicit not-wired — not mockMemories theater', () => {
    const src = readPage('page.tsx');
    expect(isHonest(src)).toBe(true);
    expect(src).not.toMatch(/\bconst\s+mockMemories\b/);
  });

  it('new/page.tsx wires live create or disabled honesty — not mock-only form', () => {
    const src = readPage('new/page.tsx');
    expect(isHonest(src)).toBe(true);
    expect(src).not.toMatch(/\bconst\s+mockMemor/);
  });

  it('[id]/page.tsx uses live get/update or disabled — not mockMemory const', () => {
    const src = readPage('[id]/page.tsx');
    expect(isHonest(src)).toBe(true);
    expect(src).not.toMatch(/\bconst\s+mockMemory\b/);
  });

  it('memory pages never call Memory Service or supervisor memory RPC', () => {
    for (const rel of ['page.tsx', 'new/page.tsx', '[id]/page.tsx']) {
      const src = readPage(rel);
      expect(src).not.toMatch(/localhost:809[0-9]/);
      expect(src).not.toMatch(/\/v1\/memory\//);
      expect(src).not.toMatch(/\/v1\/sessions\/.*\/memory\/(compact|recall)/);
    }
  });
});
