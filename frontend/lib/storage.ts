/**
 * localStorage access that never throws. Storage can be blocked (private
 * mode, sandboxed iframes, disabled site data), so every read and write is
 * wrapped and the UI keeps working with in-memory state.
 */
export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStorage(key: string, value: string): boolean {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** Parses a stored JSON value and returns it only if `guard` accepts it. */
export function readStorageJson<T>(key: string, guard: (value: unknown) => value is T): T | null {
  const raw = readStorage(key);
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return guard(parsed) ? parsed : null;
  } catch {
    return null;
  }
}
