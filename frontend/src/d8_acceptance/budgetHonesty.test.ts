/**
 * D8 §5 C / G6 — Settings Budget must not be monthly-cap theater.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

describe('D8 Settings Budget honesty', () => {
  it('Budget tab is disabled/honest — no fake monthly inputs', () => {
    const page = readFileSync(join(root, 'app/settings/page.tsx'), 'utf8');
    expect(page).not.toMatch(/defaultValue=["']100["']/);
    expect(page).not.toMatch(/You'll receive a notification when approaching your monthly budget/);
    expect(page).toMatch(
      /not wired|disabled intentionally|BudgetSpec|total_cost|budget-settings-unavailable/i
    );
  });
});
