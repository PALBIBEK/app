import { describe, expect, it } from 'vitest';

import nextConfig from '../../../next.config.mjs';
import { securityHeaders } from '../../../security-headers.mjs';

const byKey = (headers: { key: string; value: string }[]) => Object.fromEntries(headers.map((h) => [h.key, h.value]));

describe('security headers on every page', () => {
  it('forbids framing the app, so a click cannot be stolen (Approve on a tool call)', () => {
    const headers = byKey(securityHeaders({ production: false }));
    expect(headers['Content-Security-Policy']).toContain("frame-ancestors 'none'");
    expect(headers['X-Frame-Options']).toBe('DENY');
    expect(headers['X-Content-Type-Options']).toBe('nosniff');
    expect(headers['Strict-Transport-Security']).toBeUndefined(); // dev runs on plain HTTP
  });

  it('asks for HTTPS from then on in production', () => {
    expect(byKey(securityHeaders({ production: true }))['Strict-Transport-Security']).toMatch(/^max-age=31536000/);
  });

  it('applies them to every route', async () => {
    const rules = await nextConfig.headers!();
    expect(rules).toHaveLength(1);
    expect(rules[0].source).toBe('/:path*');
    expect(byKey(rules[0].headers)['X-Frame-Options']).toBe('DENY');
  });
});
