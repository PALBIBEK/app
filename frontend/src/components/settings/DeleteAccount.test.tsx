/**
 * Deleting the account asks for the password, signs out once done, and says why when the
 * account was kept.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const deleteAccount = vi.hoisted(() => vi.fn());
const replace = vi.hoisted(() => vi.fn());
const clearAuth = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api/endpoints/auth', () => ({ authAPI: { deleteAccount } }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace }) }));
vi.mock('@/lib/store/authStore', () => ({
  useAuthStore: (pick: (s: { clearAuth: () => void }) => unknown) => pick({ clearAuth }),
}));

import { DeleteAccount } from './DeleteAccount';

function submit(password: string): void {
  fireEvent.change(screen.getByLabelText('Your password'), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: 'Delete my account' }));
}

beforeEach(() => {
  deleteAccount.mockReset();
  replace.mockReset();
  clearAuth.mockReset();
});
afterEach(cleanup);

describe('DeleteAccount', () => {
  it('cannot be submitted without the password', () => {
    render(<DeleteAccount />);
    expect((screen.getByRole('button', { name: 'Delete my account' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('deletes with the password, then signs out', async () => {
    deleteAccount.mockResolvedValue(undefined);
    render(<DeleteAccount />);
    submit('my long password');
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login'));
    expect(deleteAccount).toHaveBeenCalledWith('my long password');
    expect(clearAuth).toHaveBeenCalled();
  });

  it('says why when the account was kept, and stays signed in', async () => {
    deleteAccount.mockRejectedValue(new Error('Password is incorrect'));
    render(<DeleteAccount />);
    submit('wrong');
    expect((await screen.findByRole('alert')).textContent).toBe('Password is incorrect');
    expect(clearAuth).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});
