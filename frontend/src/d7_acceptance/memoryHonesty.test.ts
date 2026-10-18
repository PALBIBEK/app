/**
 * D7 §5 D — Memory UI honesty (disabled empty state, no mock theater).
 */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = join(__dirname, '..');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

describe('D7 Memory outer honesty', () => {
  it('Sidebar does not advertise live /memory nav', () => {
    const src = read('components/layout/Sidebar.tsx');
    expect(src).not.toMatch(/href:\s*['"]\/memory['"]/);
    expect(src).toMatch(/Memory nav stays hidden|not available|D7/i);
  });

  it('/memory pages use unavailable empty state — not mockMemories', () => {
    const page = read('app/memory/page.tsx');
    expect(page).toMatch(/MemoryUnavailable|not available yet/i);
    expect(page).not.toMatch(/\bmockMemories\b/);
    expect(page).not.toMatch(/\bmockMemory\b/);

    const neu = read('app/memory/new/page.tsx');
    expect(neu).toMatch(/MemoryUnavailable|not available yet/i);
    expect(neu).not.toMatch(/\bmockMemories\b/);

    const detail = read('app/memory/[id]/page.tsx');
    expect(detail).toMatch(/MemoryUnavailable|not available yet/i);
    expect(detail).not.toMatch(/\bmockMemory\b/);
  });

  it('shared unavailable copy matches harness 422', () => {
    const path = join(root, 'components/memory/MemoryUnavailable.tsx');
    expect(existsSync(path)).toBe(true);
    const src = read('components/memory/MemoryUnavailable.tsx');
    expect(src).toMatch(/Memory stores are not available yet/);
  });
});
