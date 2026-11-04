import { api } from "../api";
import { humanize, usePoll } from "../lib";
import { Empty, ErrorLine, Panel, Pill } from "./ui";

/** What Drishti has learned from CONFIRMED / FALSE ALARM clicks, with the numbers behind every decision. */
export function Learned({ tenantId }: { tenantId: string }) {
  const l = usePoll(() => api.learning(tenantId), 20_000, [tenantId]);
  return (
    <Panel kicker="Learning" title="Learned from your feedback">
      <p className="text-[12px] mb-3" style={{ color: "var(--muted)" }}>
        A lone click never mutes an alert. A rule is quieted for an hour of the day only after repeated false alarms ({l.data?.min_evidence ?? 2}+),
        old feedback fades (half-life {l.data?.half_life_days ?? 14} days), and emergencies such as abuse, fire, weapons and falls are never muted.
      </p>
      {l.error && <ErrorLine msg={l.error} />}
      {l.data && l.data.items.length === 0 && <Empty>No feedback yet. Use CONFIRMED or FALSE ALARM on an alert.</Empty>}
      <div className="space-y-1.5">
        {l.data?.items.map((i) => (
          <div key={`${i.rule}-${i.hour}`} className="flex flex-wrap items-center gap-2 text-sm">
            <Pill color={i.muted ? "var(--warn)" : "var(--ok)"}>{i.muted ? "QUIETED" : "ACTIVE"}</Pill>
            <span className="font-semibold">{humanize(i.rule)}</span>
            <span className="mono text-[11px]" style={{ color: "var(--muted)" }}>
              around {String(i.hour).padStart(2, "0")}:00 · {i.false} false · {i.confirmed} confirmed · P(false) {i.p_false}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
