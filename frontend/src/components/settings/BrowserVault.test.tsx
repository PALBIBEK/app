import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { operateAPI } from '@/lib/api/endpoints/operate';
import { useAuthStore } from '@/lib/store/authStore';

import { BrowserVault, sitesOf, vaultLabel } from './BrowserVault';

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BrowserVault />
    </QueryClientProvider>,
  );
}

const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

beforeEach(() => {
  useAuthStore.setState({ user: { id: 'u', email: 'u@example.com', name: 'U', role: 'member' }, isAuthenticated: true, accessToken: 't' });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => vi.restoreAllMocks());

describe('BrowserVault', () => {
  it('lists saved items by name only', async () => {
    vi.spyOn(operateAPI, 'vaultItems').mockResolvedValue([
      { handle: 'login:https://shop.example.com', kind: 'login', name: 'https://shop.example.com' },
      { handle: 'card:Visa-4242', kind: 'card', name: 'Visa-4242' },
    ]);
    show();

    expect((await screen.findByTestId('vault-card:Visa-4242')).textContent).toContain('Visa-4242');
    expect(screen.getByTestId('vault-login:https://shop.example.com').textContent).toContain('https://shop.example.com');
  });

  it('saves a login with its authenticator key, and only once it has what it needs', async () => {
    vi.spyOn(operateAPI, 'vaultItems').mockResolvedValue([]);
    const save = vi.spyOn(operateAPI, 'saveVaultLogin').mockResolvedValue(undefined);
    show();
    await screen.findByText('Nothing saved yet');

    type('Site', 'https://shop.example.com');
    type('Username or email', 'ada');
    const button = screen.getByRole('button', { name: 'Save login' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    type('Password', 'pw');
    type(/Authenticator key/, 'JBSWY3DPEHPK3PXP');
    await act(async () => fireEvent.click(button));

    expect(save).toHaveBeenCalledWith({ origin: 'https://shop.example.com', username: 'ada', password: 'pw', otp: 'JBSWY3DPEHPK3PXP' });
  });

  it('saves a card for its sites, its number without spaces', async () => {
    vi.spyOn(operateAPI, 'vaultItems').mockResolvedValue([]);
    const save = vi.spyOn(operateAPI, 'saveVaultCard').mockResolvedValue(undefined);
    show();
    await screen.findByText('Nothing saved yet');

    fireEvent.click(screen.getByRole('button', { name: 'Card' }));
    type('Name', 'My Visa');
    type('Sites it may be filled on', 'https://shop.example.com, https://pay.example.com');
    type('Card number', '4242 4242 4242 4242');
    type('Month', '04');
    type('Year', '2031');
    type('CVC', '731');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save card' })));

    expect(save).toHaveBeenCalledWith({
      label: 'My-Visa',
      origins: ['https://shop.example.com', 'https://pay.example.com'],
      card_number: '4242424242424242',
      exp_month: '04',
      exp_year: '2031',
      cvc: '731',
    });
  });

  it('deletes an item by its handle', async () => {
    vi.spyOn(operateAPI, 'vaultItems').mockResolvedValue([{ handle: 'address:Home', kind: 'address', name: 'Home' }]);
    const del = vi.spyOn(operateAPI, 'deleteVaultItem').mockResolvedValue(undefined);
    show();

    fireEvent.click(await screen.findByRole('button', { name: 'Delete address:Home' }));

    await waitFor(() => expect(del).toHaveBeenCalledWith('address:Home'));
  });
});

describe('labels and sites', () => {
  it('makes a label the vault takes, and splits sites', () => {
    expect(vaultLabel('  My Visa card! ')).toBe('My-Visa-card');
    expect(sitesOf('https://a.com,https://b.com\nhttps://c.com')).toEqual(['https://a.com', 'https://b.com', 'https://c.com']);
  });
});
