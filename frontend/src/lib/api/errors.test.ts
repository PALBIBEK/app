import { describe, expect, it } from 'vitest';

import { serverMessage } from './errors';

describe('serverMessage', () => {
  it('reads each shape the backend answers with', () => {
    expect(serverMessage({ detail: 'Docker is not reachable' })).toBe('Docker is not reachable');
    expect(serverMessage({ detail: { error_code: 'X', message: 'not bound' } })).toBe('not bound');
    expect(serverMessage({ detail: [{ msg: 'a' }, { msg: 'b' }] })).toBe('a; b');
  });

  it('is null when there is no reason to show', () => {
    expect(serverMessage(undefined)).toBeNull();
    expect(serverMessage('<html>')).toBeNull();
    expect(serverMessage({ detail: { error_code: 'X' } })).toBeNull();
  });
});

describe('getApiErrorMessage', () => {
  it('prefers the server reason, then the error, then the fallback', async () => {
    const { AxiosError } = await import('axios');
    const { getApiErrorMessage } = await import('./errors');
    const err = new AxiosError('Request failed with status code 422');
    err.response = { data: { detail: { message: 'not bound' } } } as never;
    expect(getApiErrorMessage(err, 'x')).toBe('not bound');
    expect(getApiErrorMessage(new Error('boom'), 'x')).toBe('boom');
    expect(getApiErrorMessage(null, 'x')).toBe('x');
  });
});
