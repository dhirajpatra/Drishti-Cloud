import type { Actuation, EventRow } from "../api";
import { clock, humanize, LEVEL_COLOR, relTime, useNow } from "../lib";
import { Chip, Empty, ErrorLine, Panel, Pill } from "./ui";

const DECISION_COLOR: Record<string, string> = { CRITICAL: LEVEL_COLOR.CRITICAL, ALERT: LEVEL_COLOR.ALERT, SUPPRESSED: "#a78bfa" };

export function EventTimeline({ events, loading, error }: { events: EventRow[] | undefined; loading: boolean; error: string | null }) {
  const rows = events?.slice(0, 30);
  return (
    <Panel kicker="EVENT TIMELINE" title="Recent events">
      <ErrorLine msg={error} />
      {loading && !events && <Empty>Loading events...</Empty>}
      {events && events.length === 0 && <Empty>No events yet. Send a test frame.</Empty>}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
          <table className="w-full text-left min-w-[520px]">
            <thead>
              <tr className="mono text-[10px] tracking-widest sticky top-0" style={{ color: "var(--muted)", background: "var(--surface)" }}>
                <th className="py-1.5 pr-3 font-normal">TIME</th><th className="pr-3 font-normal">CAMERA</th><th className="pr-3 font-normal">LEVEL</th><th className="pr-3 font-normal">DECISION</th><th className="font-normal">REASON</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={`${e.frame_id}-${e.created_at}`} className="border-t text-[12px] align-top" style={{ borderColor: "var(--line)" }}>
                  <td className="mono py-1.5 pr-3 whitespace-nowrap">{clock(e.created_at)}</td>
                  <td className="mono pr-3 whitespace-nowrap" style={{ color: "var(--muted)" }}>{e.camera_id}</td>
                  <td className="pr-3"><Pill color={LEVEL_COLOR[e.level]}>{e.level}</Pill></td>
                  <td className="pr-3">{e.decision ? <Pill color={DECISION_COLOR[e.decision] ?? "#8ea3c2"}>{e.decision}</Pill> : <span style={{ color: "var(--muted)" }}>-</span>}</td>
                  <td className="py-1.5">{e.reason || <span style={{ color: "var(--muted)" }}>-</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

const STATUS_COLOR = (s: string): string => {
  const l = s.toLowerCase();
  if (/(ok|sent|deliver|done|success|ack)/.test(l)) return "#34d399";
  if (/(fail|error|reject)/.test(l)) return "#f87171";
  return "#fbbf24";
};

export function ActuationList({ items, loading, error }: { items: Actuation[] | undefined; loading: boolean; error: string | null }) {
  const now = useNow(1000);
  return (
    <Panel kicker="ACTUATIONS" title="Edge and notifications">
      <ErrorLine msg={error} />
      {loading && !items && <Empty>Loading actuations...</Empty>}
      {items && items.length === 0 && <Empty>Nothing sent yet.</Empty>}
      <div className="space-y-2 max-h-[320px] overflow-y-auto pr-1">
        {items?.map((a) => (
          <div key={a.id} className="rounded-lg border px-3 py-2" style={{ borderColor: "var(--line)", background: "#05080f" }}>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold text-sm">{humanize(a.kind)}</span>
              <Pill color={STATUS_COLOR(a.status)}>{a.status}</Pill>
              <span className="mono text-[10px] ml-auto" style={{ color: "var(--muted)" }}>{relTime(a.created_at, now)}</span>
            </div>
            {Object.keys(a.payload ?? {}).length > 0 && (
              <p className="mono text-[10px] mt-1 break-all" style={{ color: "var(--muted)" }}>{JSON.stringify(a.payload).slice(0, 220)}</p>
            )}
            <div className="mt-1"><Chip>alert {a.alert_id.slice(0, 8)}</Chip></div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
