import { describe, expect, it } from 'vitest';

import { AuthResponseSchema } from './auth.types';

// A login response exactly as the backend sends it for a user who never set an avatar.
const login = {
  user: {
    email: 'demo@example.com',
    name: 'Demo',
    role: 'member',
    avatar: null,
    id: 'cc56c470-8ffd-452b-a5e3-c71bc47db906',
    is_active: true,
    created_at: '2026-10-21T09:36:19.346020Z',
    updated_at: '2026-10-21T09:36:19.346025Z',
  },
  access_token: 'token',
  token_type: 'bearer',
  csrf_token: 'csrf',
};

describe('AuthResponseSchema', () => {
  it('accepts a user without an avatar, as the backend sends one', () => {
    expect(AuthResponseSchema.parse(login).user.avatar).toBeNull();
  });
});
