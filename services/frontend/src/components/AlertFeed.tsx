import { useEffect, useRef, useState } from "react";
import { Check, ImageOff, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { api, type Alert } from "../api";
import { humanize, LEVEL_COLOR, relTime, useNow } from "../lib";
import { Chip, Empty, ErrorLine, Panel, Pill, btnGhost } from "./ui";

type ImgCache = Map<string, string>;
type Verdict = "ack" | "confirmed" | "false_alarm";

function AlertImage({ id, cache }: { id: string; cache: React.MutableRefObject<ImgCache> }) {
  const [url, setUrl] = useState<string | undefined>(cache.current.get(id));
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const hit = cache.current.get(id);
    if (hit) { setUrl(hit); return; }
    let alive = true;
    api.alertImageBlob(id).then((b) => {
      const u = URL.createObjectURL(b);
      if (!alive) { URL.revokeObjectURL(u); return; }
      cache.current.set(id, u);
      setUrl(u);
    }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [id, cache]);
  if (failed) return <div className="w-full sm:w-40 h-28 rounded-lg border grid place-items-center shrink-0" style={{ borderColor: "var(--line)", color: "var(--muted)" }}><ImageOff size={18} /></div>;
  if (!url) return <div className="w-full sm:w-40 h-28 rounded-lg shrink-0 animate-pulse" style={{ background: "var(--field)" }} />;
  return <a href={url} target="_blank" rel="noreferrer" className="shrink-0 block"><img src={url} alt="Alert proof" className="w-full sm:w-40 h-28 object-cover rounded-lg border" style={{ borderColor: "var(--line)" }} /></a>;
}

function AlertCard({ a, now, cache, onAck }: { a: Alert; now: number; cache: React.MutableRefObject<ImgCache>; onAck: (id: string, verdict: Verdict) => Promise<void> }) {
  const [busy, setBusy] = useState<Verdict | null>(null);
  const c = LEVEL_COLOR[a.level];
  const act = async (v: Verdict) => {
    setBusy(v);
    try { await onAck(a.id, v); } finally { setBusy(null); }
  };
  return (
    <div className="rounded-xl border p-3 transition-opacity" style={{ borderColor: c, background: a.level === "CRITICAL" ? "var(--crit-bg)" : "var(--warn-bg)", opacity: a.acknowledged ? 0.45 : 1 }}>
      <div className="flex flex-col sm:flex-row gap-3">
        {a.has_image && <AlertImage id={a.id} cache={cache} />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Pill color={c} solid>{a.level}</Pill>
            <span className="font-semibold">{humanize(a.rule)}</span>
            {a.acknowledged && <Pill color="var(--ok)">ACKNOWLEDGED</Pill>}
            <span className="mono text-[10px] ml-auto" style={{ color: "var(--muted)" }}>{relTime(a.created_at, now)}</span>
          </div>
          <p className="text-sm mt-1.5">{a.reason}</p>
          {a.rationale && <p className="text-[12px] mt-1" style={{ color: "var(--muted)" }}><span className="mono" style={{ color: c }}>WHY </span>{a.rationale}</p>}
          {(a.objects.length > 0 || a.actions.length > 0) && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {a.objects.map((o) => <Chip key={`o-${o}`}>{o}</Chip>)}
              {a.actions.map((x) => <Chip key={`a-${x}`} color="var(--accent)">{"> "}{x}</Chip>)}
            </div>
          )}
          {!a.acknowledged && (
            <div className="flex gap-2 mt-3">
              <button disabled={busy !== null} onClick={() => act("ack")} className="mono text-[11px] px-3 py-1.5 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={{ borderColor: c, color: c }}><Check size={12} />{busy === "ack" ? "..." : "ACKNOWLEDGE"}</button>
              <button disabled={busy !== null} onClick={() => act("confirmed")} title="A real event: Drishti learns to trust this alert" className="mono text-[11px] px-3 py-1.5 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={{ borderColor: "var(--ok)", color: "var(--ok)" }}><ShieldCheck size={12} />{busy === "confirmed" ? "..." : "CONFIRMED"}</button>
              <button disabled={busy !== null} onClick={() => act("false_alarm")} className="mono text-[11px] px-3 py-1.5 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={btnGhost}><ShieldX size={12} />{busy === "false_alarm" ? "..." : "FALSE ALARM"}</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function AlertFeed({ alerts, loading, error, reload }: { alerts: Alert[] | undefined; loading: boolean; error: string | null; reload: () => void }) {
  const now = useNow(1000);
  const cache = useRef<ImgCache>(new Map());
  const [ackErr, setAckErr] = useState<string | null>(null);
  useEffect(() => {
    const c = cache.current;
    return () => { c.forEach((u) => URL.revokeObjectURL(u)); c.clear(); };
  }, []);
  const onAck = async (id: string, v: Verdict) => {
    setAckErr(null);
    try { await api.ack(id, v === "false_alarm", v === "confirmed"); reload(); } catch (e) { setAckErr(e instanceof Error ? e.message : String(e)); }
  };
  const open = alerts?.filter((a) => !a.acknowledged).length ?? 0;
  return (
    <Panel kicker="LIVE ALERT FEED" title="Alerts" right={<span className="mono text-[11px] flex items-center gap-1.5" style={{ color: open ? "var(--bad)" : "var(--muted)" }}><ShieldAlert size={13} />{open} open</span>}>
      <ErrorLine msg={error} />
      <ErrorLine msg={ackErr} />
      {loading && !alerts && <Empty>Loading alerts...</Empty>}
      {alerts && alerts.length === 0 && <Empty>No alerts. Quiet so far.</Empty>}
      <div className="space-y-3 max-h-[640px] overflow-y-auto pr-1">
        {alerts?.map((a) => <AlertCard key={a.id} a={a} now={now} cache={cache} onAck={onAck} />)}
      </div>
    </Panel>
  );
}
