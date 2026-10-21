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
  it('the chat answers approvals through the BFF', () => {
    const chat = read('components/session/SessionChat.tsx');
    const parts = read('components/session/parts.tsx');
    expect(chat).toMatch(/sessionsAPI\.respondApproval/);
    expect(parts).toMatch(/approval-approve/);
    expect(parts).toMatch(/approval-deny/);
    for (const src of [chat, parts]) expect(src).not.toMatch(/localhost:8080.*approvals|\/v1\/approvals/);
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
