import { useEffect, useRef, useState } from "react";
import { Bell, CheckCheck, Newspaper, ShieldAlert, VideoOff } from "lucide-react";
import { api, type Notification } from "../api";
import { relTime, useNow, usePoll } from "../lib";
import { Empty, ErrorLine, Panel, Pill, btnGhost } from "./ui";

const COLOR = { CRITICAL: "#f87171", WARNING: "#fbbf24", INFO: "#60a5fa" } as const;

/** Per-tenant notification centre: local-news hazards and confirmed alerts. Polls every 10 s; the page title shows the unread count. */
export function Notifications({ tenantId }: { tenantId: string }) {
  const list = usePoll(() => api.notifications(tenantId), 10_000, [tenantId]);
  const now = useNow(15_000);
  const [busy, setBusy] = useState(false);
  const base = useRef(document.title.replace(/^\(\d+\)\s*/, ""));
  const unread = list.data?.unread ?? 0;

  useEffect(() => {
    document.title = unread > 0 ? `(${unread}) ${base.current}` : base.current;
    return () => { document.title = base.current; };
  }, [unread]);

  const markRead = async (ids?: string[]) => {
    setBusy(true);
    try { await api.markNotificationsRead(tenantId, ids); list.reload(); } finally { setBusy(false); }
  };

  const items: Notification[] = list.data?.items ?? [];
  return (
    <Panel kicker="NOTIFICATIONS" title="Local news, alerts &amp; camera health"
      right={
        <div className="flex items-center gap-2">
          <span className="mono text-[11px] flex items-center gap-1.5" style={{ color: unread ? COLOR.WARNING : "var(--muted)" }}>
            <Bell size={12} />{unread} unread
          </span>
          {unread > 0 && (
            <button disabled={busy} onClick={() => markRead()} className="mono text-[11px] px-2.5 py-1 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={btnGhost}>
              <CheckCheck size={12} />MARK ALL READ
            </button>
          )}
        </div>
      }>
      <ErrorLine msg={list.error} />
      {items.length === 0 ? (
        <Empty>{list.loading && !list.data ? "Loading..." : "Nothing to report. Hazard news near this site and confirmed alerts show up here."}</Empty>
      ) : (
        <ul className="space-y-2 max-h-72 overflow-y-auto pr-1">
          {items.map((n) => {
            const c = COLOR[n.level] ?? COLOR.INFO;
            const Icon = n.kind === "news" ? Newspaper : n.kind === "camera" ? VideoOff : ShieldAlert;
            return (
              <li key={n.id} onClick={() => !n.read && markRead([n.id])} className="rounded-lg border p-2.5 flex gap-2.5 cursor-pointer"
                style={{ borderColor: n.read ? "var(--line)" : c, opacity: n.read ? 0.55 : 1, background: n.read ? "transparent" : "#0b1424" }}>
                <Icon size={16} style={{ color: c }} className="mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill color={c} solid={!n.read}>{n.level}</Pill>
                    <span className="mono text-[10px]" style={{ color: "var(--muted)" }}>{n.kind.toUpperCase()}</span>
                    <span className="mono text-[10px] ml-auto" style={{ color: "var(--muted)" }}>{relTime(n.ts, now)}</span>
                  </div>
                  <p className="text-sm mt-1 break-words">
                    {n.url ? <a href={n.url} target="_blank" rel="noreferrer" className="underline" onClick={(e) => e.stopPropagation()}>{n.title}</a> : n.title}
                  </p>
                  {n.detail && <p className="text-[12px] mt-0.5 break-words" style={{ color: "var(--muted)" }}>{n.detail}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
