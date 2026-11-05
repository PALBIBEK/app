/**
 * Keep a conversation scrolled to its end as it grows.
 *
 * The effect's body is a block on purpose: an effect may return only a cleanup function or
 * nothing (React: "Effects must not return anything besides a function, which is used for
 * clean-up"). `Element.scrollIntoView()` returned `undefined` in every browser until Chrome
 * 156, which returns a Promise; an arrow that returned its result handed React that Promise as
 * the cleanup, and React called it on the next update and threw ("destroy is not a function"),
 * crashing the chat page.
 */

import { useEffect, type DependencyList, type RefObject } from 'react';

export function useScrollToEnd(end: RefObject<HTMLElement | null>, deps: DependencyList): void {
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps -- the caller says when the end moved
}
