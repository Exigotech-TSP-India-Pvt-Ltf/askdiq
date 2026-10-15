"use client";

import { useCallback, useState } from "react";

/**
 * Keyset pagination state for cursor endpoints (`next_cursor` envelopes):
 * a stack of the cursors used to reach each page, so "Older" pushes the
 * cursor the server returned and "Newer" pops back. Changing `resetKey`
 * (filters, audience, range) returns to the first page.
 */
export function useCursorStack(resetKey: string) {
  const [state, setState] = useState<{ key: string; stack: (string | null)[] }>({ key: resetKey, stack: [null] });
  const stack = state.key === resetKey ? state.stack : [null];
  if (state.key !== resetKey) setState({ key: resetKey, stack: [null] });

  const next = useCallback(
    (cursor: string | null | undefined) => {
      if (!cursor) return;
      setState((s) => ({ key: resetKey, stack: [...(s.key === resetKey ? s.stack : [null]), cursor] }));
    },
    [resetKey],
  );
  const prev = useCallback(() => {
    setState((s) => ({ key: resetKey, stack: s.stack.length > 1 ? s.stack.slice(0, -1) : s.stack }));
  }, [resetKey]);

  return {
    cursor: stack[stack.length - 1],
    page: stack.length,
    hasPrev: stack.length > 1,
    next,
    prev,
  };
}
