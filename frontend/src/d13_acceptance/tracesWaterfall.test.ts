/**
 * D13 — FE span waterfall: /traces/[id] fetches BFF spans (not redirect-only).
 * Honesty checks in tracesHonesty.test.ts must still pass.
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

describe('D13 Trace span waterfall', () => {
  it('/traces/[id] loads spans from BFF Trace API (not redirect-only)', () => {
    const detail = read('app/traces/[id]/page.tsx');
    expect(existsSync(join(root, 'app/traces/[id]/page.tsx'))).toBe(true);
    expect(detail).not.toMatch(/router\.(replace|push)\([`'"]\/sessions\//);
    expect(detail).toMatch(/tracesAPI|\/traces\/.+\/spans|getSpans/);
    expect(detail).toMatch(/\/api\/v1\/traces|tracesAPI\.getSpans|\/traces\/\$\{/);
    // Waterfall / span tree signal
    expect(detail).toMatch(/span|waterfall|Span/i);
  });

  it('honest empty state when no spans + session timeline link retained', () => {
    const detail = read('app/traces/[id]/page.tsx');
    expect(detail).toMatch(/No spans|no spans|empty/i);
    expect(detail).toMatch(/\/sessions\//);
    expect(detail).toMatch(/Session timeline|session timeline/i);
  });

  it('tracesAPI endpoint targets BFF /traces/{id}/spans', () => {
    const ep = read('lib/api/endpoints/traces.ts');
    expect(ep).toMatch(/\/traces\/.+\/spans/);
    expect(ep).not.toMatch(/16686|jaeger/i);
  });
});
