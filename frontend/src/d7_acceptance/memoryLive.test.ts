/**
 * D7 — /memory is live: the user's notebooks through the product backend, never the
 * memory service or the supervisor from the browser, and no made-up data.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => readFileSync(join(__dirname, '..', rel), 'utf8');

describe('D7 /memory is live', () => {
  it('the page reads and changes notebooks through the memory hooks', () => {
    const page = read('app/memory/page.tsx');
    for (const hook of ['useNotebooks', 'useFacts', 'useAddFacts', 'useForgetFacts', 'useRevisions', 'useRestoreNotebook', 'useSearchMemory']) {
      expect(page).toMatch(new RegExp(`\\b${hook}\\b`));
    }
    expect(page).not.toMatch(/\bmockMemor/);
  });

  it('an outage is said plainly, not shown as an empty memory', () => {
    expect(read('app/memory/page.tsx')).toMatch(/memory-unavailable/);
  });

  it('the old memory routes lead to the page', () => {
    for (const rel of ['app/memory/[id]/page.tsx', 'app/memory/new/page.tsx']) expect(read(rel)).toMatch(/redirect\('\/memory'\)/);
  });

  it('the sidebar links to it', () => {
    expect(read('components/layout/Sidebar.tsx')).toMatch(/href: '\/memory'/);
  });

  it('nothing in the browser calls the memory service or the supervisor', () => {
    for (const rel of ['app/memory/page.tsx', 'lib/api/endpoints/memory.ts']) {
      const src = read(rel);
      expect(src).not.toMatch(/localhost:809[0-9]|localhost:8080/);
      expect(src).not.toMatch(/['"`]\/v1\/memory/);
    }
  });
});
