/**
 * A browser whose scrollIntoView returns a Promise (Chrome 156) must not crash the chat: the
 * effect returns nothing, so React has no "cleanup" to call. React reports the crash as an
 * uncaught "destroy is not a function" (not a synchronous throw), so the test watches for it.
 */
import { renderHook } from '@testing-library/react';
import { Component, useEffect, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useScrollToEnd } from './useScrollToEnd';

const errors = vi.spyOn(console, 'error').mockImplementation(() => {});

beforeEach(() => errors.mockClear());

/** Keeps the crash inside the test: React hands a commit-phase error to the nearest boundary
 * (``captureCommitPhaseError``) instead of rethrowing it from the root, and still reports it. */
class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }
  render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}

function reported(): string {
  return errors.mock.calls.map((c) => c.map(String).join(' ')).join('\n');
}

function scrollingEnd() {
  const scroll = vi.fn(() => Promise.resolve());
  return { scroll, end: { current: { scrollIntoView: scroll } as unknown as HTMLElement } };
}

describe('useScrollToEnd', () => {
  it('scrolls on each change and survives a scrollIntoView that returns a Promise', () => {
    const { scroll, end } = scrollingEnd();
    const { rerender, unmount } = renderHook(({ n }) => useScrollToEnd(end, [n]), {
      initialProps: { n: 1 },
      wrapper: Boundary,
    });
    rerender({ n: 2 }); // React runs the previous effect's cleanup here
    unmount();
    expect(reported()).not.toMatch(/destroy is not a function|must not return anything/);
    expect(scroll).toHaveBeenCalledTimes(2);
    expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'nearest' });
  });

  it('the check catches the crash it guards against (an effect returning the Promise)', () => {
    const { end } = scrollingEnd();
    const { rerender } = renderHook(
      ({ n }) => useEffect(() => end.current.scrollIntoView() as unknown as void, [n]),
      { initialProps: { n: 1 }, wrapper: Boundary },
    );
    rerender({ n: 2 });
    expect(reported()).toMatch(/destroy is not a function/);
  });

  it('does nothing before the end is on the page', () => {
    const end = { current: null };
    renderHook(() => useScrollToEnd(end, [1]));
    expect(reported()).toBe('');
  });
});
