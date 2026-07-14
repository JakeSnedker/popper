import { useCallback, useEffect, useState } from "react";

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = [], intervalMs = 0): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const memoFn = useCallback(fn, deps);

  const run = useCallback(() => {
    let active = true;
    setLoading(true);
    setError(null);
    memoFn()
      .then((d) => active && setData(d))
      .catch((e: Error) => active && setError(e.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [memoFn]);

  useEffect(() => run(), [run]);

  useEffect(() => {
    if (!intervalMs) return;
    const id = window.setInterval(() => {
      memoFn()
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs, memoFn]);

  return { data, loading, error, reload: run };
}
