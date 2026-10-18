/**
 * D8 T16 / §5 D — Test harness HITL Approve/Deny via BFF (no mock path).
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = join(__dirname, '..');

function read(rel: string): string {
  return readFileSync(join(root, rel), 'utf8');
}

describe('D8 T16 Test HITL live approvals', () => {
  it('Test page wires listApprovals + respondApproval (BFF)', () => {
    const page = read('app/harness/[id]/test/page.tsx');
    expect(page).toMatch(/listApprovals/);
    expect(page).toMatch(/respondApproval/);
    expect(page).toMatch(/approval-approve|approval-deny|Approval required/);
    expect(page).not.toMatch(/localhost:8080.*approvals|\/v1\/approvals/);
  });

  it('send is disabled while awaiting_approval', () => {
    const page = read('app/harness/[id]/test/page.tsx');
    expect(page).toMatch(/awaitingApproval|awaiting_approval/);
    expect(page).toMatch(/sendBlocked/);
  });

  it('wire reducer handles approval_required / approval_resolved', () => {
    const reducer = read('lib/session/wireEventReducer.ts');
    expect(reducer).toMatch(/approval_required/);
    expect(reducer).toMatch(/approval_resolved/);
    expect(reducer).toMatch(/pendingApprovals/);
  });

  it('sessionsAPI exposes BFF approval endpoints', () => {
    const api = read('lib/api/endpoints/sessions.ts');
    expect(api).toMatch(/listApprovals/);
    expect(api).toMatch(/respondApproval/);
    expect(api).toMatch(/\/sessions\/\$\{.*\}\/approvals/);
  });
});
