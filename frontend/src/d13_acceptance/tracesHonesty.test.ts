/**
 * D13 — Traces UI honesty: live sessions (no mock IDs); session timeline path exists.
 * Jaeger link/docs optional. Span waterfall covered in tracesWaterfall.test.ts.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

describe('D13 Traces UI honesty', () => {
  it('/traces list uses live sessions — no hard-coded mock trace/session IDs', () => {
    const page = read('app/traces/page.tsx');
    expect(page).not.toMatch(/\bmockTraces\b/);
    expect(page).not.toMatch(/\bmockSessions\b/);
    expect(page).not.toMatch(/\bMOCK_TRACE/);
    expect(page).not.toMatch(/\bfake-trace-/i);
    expect(page).not.toMatch(/trace-[0-9a-f]{8}-mock/i);
    // Live BFF sessions hook or equivalent fetch — not a static fixture array of IDs.
    expect(page).toMatch(/useSessions|sessionsAPI|\/sessions/);
    expect(page).not.toMatch(
      /const\s+(traces|mockTraces)\s*=\s*\[[^\]]*(id:\s*['\"]sess_|id:\s*['\"]trace_)/s
    );
  });

  it('session timeline path exists from /traces/[id] (D4 session Traces remain available)', () => {
    const detail = read('app/traces/[id]/page.tsx');
    expect(existsSync(join(root, 'app/traces/[id]/page.tsx'))).toBe(true);
    // Redirect or render toward live session timeline — not a hard-coded mock id.
    expect(detail).not.toMatch(/\bmockTrace\b/);
    expect(detail).not.toMatch(/trace-id-hardcoded|HARDCODED_TRACE/i);
    const hasTimeline =
      /\/sessions\/\$\{|\/sessions\/`|router\.(replace|push)\([`'"]\/sessions\//.test(
        detail
      ) ||
      /useSession|SessionTimeline|session timeline/i.test(detail) ||
      /\/sessions\//.test(detail);
    expect(
      hasTimeline,
      '/traces/[id] must retain a live session timeline link (D4) alongside waterfall'
    ).toBe(true);
  });

  it('Jaeger deep-link is optional — if present must not replace live sessions list', () => {
    const page = read('app/traces/page.tsx');
    if (/jaeger|16686/i.test(page)) {
      expect(page).toMatch(/useSessions|sessionsAPI|\/sessions/);
      // Docs-style external link OK; must not be the only data source.
      expect(page).not.toMatch(/const\s+traces\s*=\s*await\s+fetch\(['\"]http:\/\/.*16686/);
    }
  });
});
