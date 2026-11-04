import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** Run `fn` whenever `deps` change or reload() is called. Stale responses are ignored. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });
  const [nonce, setNonce] = useState(0);
  const tick = useRef(0);

  useEffect(() => {
    const mine = ++tick.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => {
        if (mine === tick.current) setState({ data, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (mine === tick.current) {
          setState((s) => ({ ...s, error: errorMessage(err), loading: false }));
        }
      });
    return () => {
      tick.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reload: () => setNonce((n) => n + 1) };
}