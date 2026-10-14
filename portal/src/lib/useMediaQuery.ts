import { useCallback, useSyncExternalStore } from 'react';

/** CSS medya sorgusunu izler; matchMedia yoksa (ör. jsdom) `fallback` döner. */
export function useMediaQuery(query: string, fallback = false): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window.matchMedia !== 'function') return () => undefined;
      const mql = window.matchMedia(query);
      mql.addEventListener('change', onChange);
      return () => {
        mql.removeEventListener('change', onChange);
      };
    },
    [query],
  );
  const getSnapshot = () =>
    typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : fallback;
  return useSyncExternalStore(subscribe, getSnapshot, () => fallback);
}
