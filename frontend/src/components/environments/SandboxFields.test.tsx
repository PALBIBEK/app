/**
 * The sandbox form offers a browser agent what it needs: what the sandbox may reach (with the
 * hosts, for an allow list) and where the browser runs, and warns when it could reach no site.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SandboxSpec } from '@/lib/api/types/manifest.types';

import { SandboxFields } from './SandboxFields';

afterEach(cleanup);

const docker: SandboxSpec = { provider: 'local_docker', file_effects: 'workspace_write', approval_default: 'ask' };

describe('SandboxFields for a browser agent', () => {
  it('offers the browser only to a harness with browser tools', () => {
    render(<SandboxFields value={docker} tools={['read', 'bash']} />);
    expect(screen.queryByLabelText('The browser')).toBeNull();
    cleanup();
    render(<SandboxFields value={docker} tools={['browser_exec']} />);
    expect(screen.getByLabelText('The browser')).toBeTruthy();
    expect(screen.getByTestId('browser-offline').textContent).toMatch(/reaches nothing/);
  });

  it('opens the web, and an allow list takes hosts as typed', () => {
    const onChange = vi.fn();
    const { rerender } = render(<SandboxFields value={docker} onChange={onChange} tools={['browser_exec']} />);
    fireEvent.change(screen.getByLabelText('What the sandbox may reach'), { target: { value: 'open' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...docker, network: { mode: 'open' } });

    const allow = { ...docker, network: { mode: 'allow' as const, hosts: [] } };
    rerender(<SandboxFields value={allow} onChange={onChange} tools={['browser_exec']} />);
    fireEvent.change(screen.getByLabelText('Hosts'), { target: { value: 'cdp.example.com, ' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...docker, network: { mode: 'allow', hosts: ['cdp.example.com'] } });
    expect((screen.getByLabelText('Hosts') as HTMLInputElement).value).toBe('cdp.example.com, ');
  });

  it('picks a remote browser, which needs no warning about the sandbox', () => {
    const onChange = vi.fn();
    render(<SandboxFields value={docker} onChange={onChange} tools={['browser_exec']} />);
    fireEvent.change(screen.getByLabelText('The browser'), { target: { value: 'remote-cdp' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...docker, browser: 'remote-cdp' });
  });

  it('names no network where the provider cannot enforce one', () => {
    render(<SandboxFields value={{ ...docker, provider: 'local_process' }} tools={['browser_exec']} />);
    expect(screen.queryByLabelText('What the sandbox may reach')).toBeNull();
  });
});
