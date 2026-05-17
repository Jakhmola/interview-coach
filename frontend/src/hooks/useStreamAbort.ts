import { useCallback, useEffect, useRef } from "react";

/**
 * Manages a single `AbortController` whose `signal` you pass into a
 * streaming call. Aborts and resets on:
 *  - Component unmount (so navigating away cancels the in-flight stream)
 *  - The next call to `fresh()` (so consecutive streams don't pile up)
 *
 * Returns `{ getSignal, fresh, abort }`:
 *  - `getSignal()` — current signal, or null if none yet
 *  - `fresh()`     — abort the previous controller (if any) and return a
 *                    new signal to hand into the next streaming call
 *  - `abort()`     — abort the current controller manually
 */
export function useStreamAbort() {
  const controllerRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    if (controllerRef.current) {
      controllerRef.current.abort();
      controllerRef.current = null;
    }
  }, []);

  const fresh = useCallback((): AbortSignal => {
    abort();
    const next = new AbortController();
    controllerRef.current = next;
    return next.signal;
  }, [abort]);

  const getSignal = useCallback((): AbortSignal | null => {
    return controllerRef.current?.signal ?? null;
  }, []);

  useEffect(() => {
    return () => {
      abort();
    };
  }, [abort]);

  return { getSignal, fresh, abort };
}
