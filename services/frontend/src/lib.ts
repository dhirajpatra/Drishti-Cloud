import { useCallback, useEffect, useRef, useState } from "react";
import { TOKEN_EVENT, type VerticalId } from "./api";

export interface PollState<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean; // true only until the first result/error arrives
  reload: () => void;
}

/** Poll an async fetcher. Keeps previous data while refetching (no layout shift). */
export function usePoll<T>(fn: (() => Promise<T>) | null, intervalMs: number, deps: unknown[] = []): PollState<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(fn !== null);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const onTok = () => setTick((t) => t + 1);
    window.addEventListener(TOKEN_EVENT, onTok);
    return () => window.removeEventListener(TOKEN_EVENT, onTok);
  }, []);

  useEffect(() => {
    if (!fnRef.current) { setData(undefined); setLoading(false); return; }
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    const run = async () => {
      const f = fnRef.current;
      if (!f) return;
      try {
        const d = await f();
        if (alive) { setData(d); setError(null); }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) { setLoading(false); timer = setTimeout(run, intervalMs); }
      }
    };
    void run();
    return () => { alive = false; if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, fn === null, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function relTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "-";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleTimeString([], { hour12: false });
}

export function humanize(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().replace(/^./, (c) => c.toUpperCase());
}

/** Re-render every `ms` so relative times stay fresh. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

export const VERTICAL_COLOR: Record<VerticalId, string> = {
  mall_security: "#fb923c",
  home_seniors: "#34d399",
  school_children: "#60a5fa",
  icu_hospital: "#f472b6",
};
export const verticalColor = (v: string): string => VERTICAL_COLOR[v as VerticalId] ?? "#8ea3c2";

export const SENSOR_PRESETS: Record<VerticalId, string> = {
  mall_security: '{"pir":true,"us_front":75,"temp":32}',
  home_seniors: '{"pir":false,"us_door":150,"temp":29,"audio_transcript":"help me"}',
  school_children: '{"pir":true}',
  icu_hospital: '{"pir":true,"temp":26}',
};

export const LEVEL_COLOR = { CRITICAL: "#f87171", ALERT: "#fbbf24", NORMAL: "#34d399" } as const;
