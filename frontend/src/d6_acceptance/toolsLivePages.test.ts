/**
 * D6 §5 F / H / LOCAL_DEMO #3 — FE /tools must be live CRUD (not mockTools theater).
 *
 * Source/string acceptance: pages import useTools (or siblings) and must not ship
 * mockTools / mockTool fixtures as the page data source.
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const toolsDir = resolve(__dirname, '../app/tools');

function readPage(rel: string): string {
  const path = resolve(toolsDir, rel);
  expect(existsSync(path), `missing ${path}`).toBe(true);
  return readFileSync(path, 'utf8');
}

describe('D6 FE /tools live pages (LOCAL_DEMO #3)', () => {
  it('page.tsx imports useTools (or useCreateTool/useTools list) — not mock-only', () => {
    const src = readPage('page.tsx');
    expect(src).toMatch(/useTools|useCreateTool|toolsAPI/);
    expect(src).not.toMatch(/\bconst\s+mockTools\b/);
    expect(src).not.toMatch(/\bmockTools\s*=/);
  });

  it('new/page.tsx wires live create (useCreateTool / toolsAPI.create)', () => {
    const src = readPage('new/page.tsx');
    expect(src).toMatch(/useCreateTool|useTools|toolsAPI\.create|toolsAPI/);
    // Must not be a dead form that never calls the API.
    expect(src).not.toMatch(/\/\/\s*TODO:\s*wire.*api/i);
  });

  it('[id]/page.tsx uses live get/update/delete hooks — not mockTool const', () => {
    const src = readPage('[id]/page.tsx');
    expect(src).toMatch(/useTool|useUpdateTool|useDeleteTool|toolsAPI/);
    expect(src).not.toMatch(/\bconst\s+mockTool\b/);
  });

  it('tools pages never call supervisor host directly', () => {
    for (const rel of ['page.tsx', 'new/page.tsx', '[id]/page.tsx']) {
      const src = readPage(rel);
      expect(src).not.toMatch(/localhost:8080/);
      expect(src).not.toMatch(/\/v1\/sessions\/.*\/tools\/invoke/);
      expect(src).not.toMatch(/\/v1\/catalog\//);
    }
  });
});
