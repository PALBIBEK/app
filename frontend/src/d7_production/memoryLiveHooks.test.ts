/**
 * D7 FULL-PROD — FE /memory must use live BFF hooks (not MemoryUnavailable-only).
 *
 * MVP d7_acceptance allows MemoryUnavailable honesty. Production reopen requires
 * live useMemory / memoryAPI wiring and Memory nav restored.
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(__dirname, '..');
const memoryDir = resolve(root, 'app/memory');

function read(rel: string): string {
  const path = resolve(root, rel);
  expect(existsSync(path), `missing ${path}`).toBe(true);
  return readFileSync(path, 'utf8');
}

function readPage(rel: string): string {
  const path = resolve(memoryDir, rel);
  expect(existsSync(path), `missing ${path}`).toBe(true);
  return readFileSync(path, 'utf8');
}

function usesLiveHooks(src: string): boolean {
  return /useMemory|useMemoryEntry|useCreateMemory|useUpdateMemory|useDeleteMemory|useSearchMemory|memoryAPI/.test(
    src,
  );
}

describe('D7 FULL-PROD FE /memory live BFF', () => {
  it('page.tsx uses live hooks — not MemoryUnavailable-only', () => {
    const src = readPage('page.tsx');
    expect(usesLiveHooks(src)).toBe(true);
    // Production must not ship unavailable-only shell as the sole UX.
    const unavailableOnly =
      /MemoryUnavailablePage/.test(src) && !usesLiveHooks(src);
    expect(unavailableOnly).toBe(false);
    expect(src).not.toMatch(/\bmockMemories\b/);
  });

  it('new/page.tsx uses live create hook', () => {
    const src = readPage('new/page.tsx');
    expect(usesLiveHooks(src)).toBe(true);
    expect(src).not.toMatch(/\bmockMemor/);
  });

  it('[id]/page.tsx uses live get/update/delete hooks', () => {
    const src = readPage('[id]/page.tsx');
    expect(usesLiveHooks(src)).toBe(true);
    expect(src).not.toMatch(/\bconst\s+mockMemory\b/);
  });

  it('Sidebar restores live /memory nav', () => {
    const src = read('components/layout/Sidebar.tsx');
    expect(src).toMatch(/href:\s*['"]\/memory['"]/);
    // Comment that hides nav forever is MVP — production restores the link.
    expect(src).not.toMatch(/Memory nav stays hidden until durable stores ship/);
  });

  it('memoryAPI targets outer BFF /memory — not Memory Service directly', () => {
    const src = read('lib/api/endpoints/memory.ts');
    expect(src).toMatch(/\/memory/);
    expect(src).not.toMatch(/localhost:809[0-9]/);
    expect(src).not.toMatch(/\/v1\/memory\//);
  });

  it('pages never call supervisor memory RPC or durable Memory HTTP', () => {
    for (const rel of ['page.tsx', 'new/page.tsx', '[id]/page.tsx']) {
      const src = readPage(rel);
      expect(src).not.toMatch(/\/v1\/memory\//);
      expect(src).not.toMatch(/\/v1\/sessions\/.*\/memory\/(compact|recall)/);
    }
  });
});
