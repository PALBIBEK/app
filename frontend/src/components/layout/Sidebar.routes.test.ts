import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

// Every sidebar link must open a real page (a link to a missing route is a 404 in the app).
const app = join(__dirname, '../../app');
const sidebar = readFileSync(join(__dirname, 'Sidebar.tsx'), 'utf8');
const hrefs = [...sidebar.matchAll(/href: '([^']+)'/g)].map((m) => m[1]);

describe('sidebar links', () => {
  it('finds the links', () => {
    expect(hrefs).toContain('/prompts');
  });

  it.each(hrefs)('%s has a page', (href) => {
    expect(existsSync(join(app, href, 'page.tsx'))).toBe(true);
  });
});
