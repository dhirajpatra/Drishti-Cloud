import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type CreatedTenant } from "../api";
import { usePoll, verticalColor } from "../lib";
import { TenantList, ApiKeyBox } from "../components/TenantList";
import { AlertFeed } from "../components/AlertFeed";
import { ActuationList, EventTimeline } from "../components/EventsActuations";
import { LiveCameras } from "../components/LiveCameras";
import { Notifications } from "../components/Notifications";
import { FeedSources } from "../components/FeedSources";
import { Inventory } from "../components/Inventory";
import { TestFrame } from "../components/TestFrame";
import { KnowledgeGraph } from "../components/KnowledgeGraph";
import { Stat } from "../components/ui";

const SEL_KEY = "drishti_selected_tenant";
const readSel = (): string | null => { try { return localStorage.getItem(SEL_KEY); } catch { return null; } };

export default function Dashboard() {
  const [selected, setSelected] = useState<string | null>(readSel);
  const [created, setCreated] = useState<CreatedTenant | null>(null);

  const tenants = usePoll(api.tenants, 5000);
  const verticals = usePoll(api.verticals, 60000);
  const usage = usePoll(api.usage, 15000);

  const tenant = useMemo(() => tenants.data?.find((t) => t.id === selected), [tenants.data, selected]);

  // Auto-select first tenant when nothing valid is selected.
  useEffect(() => {
    if (!tenants.data || tenants.data.length === 0) return;
    if (!selected || !tenants.data.some((t) => t.id === selected)) setSelected(tenants.data[0].id);
  }, [tenants.data, selected]);

  const select = useCallback((id: string) => {
    setSelected(id);
    try { localStorage.setItem(SEL_KEY, id); } catch { /* ignore */ }
  }, []);

  const tid = tenant?.id ?? null;
  const vid = tenant?.vertical ?? null;
  const alerts = usePoll(useMemo(() => (tid ? () => api.alerts(tid, 50) : null), [tid]), 3000, [tid]);
  const events = usePoll(useMemo(() => (tid ? () => api.events(tid, 30) : null), [tid]), 5000, [tid]);
  const acts = usePoll(useMemo(() => (tid ? () => api.actuations(tid, 30) : null), [tid]), 5000, [tid]);
  const graph = usePoll(useMemo(() => (tid ? () => api.graph(tid) : null), [tid]), 15000, [tid]);
  const vstats = usePoll(useMemo(() => (vid ? () => api.verticalStats(vid) : null), [vid]), 10000, [vid]);

  const verticalLabels = useMemo(() => Object.fromEntries((verticals.data ?? []).map((v) => [v.id, v.label])), [verticals.data]);
  const llmTotal = useMemo(() => {
    if (!usage.data) return undefined;
    return Object.values(usage.data.models).reduce((s, per) => s + (per.calls ?? 0), 0);
  }, [usage.data]);
  const llmSub = usage.data ? Object.entries(usage.data.models).map(([m, per]) => `${m}:${per.calls ?? 0}`).join(" ") : undefined;

  const vColor = vid ? verticalColor(vid) : "var(--accent)";

  return (
    <div className="grid lg:grid-cols-[320px_minmax(0,1fr)] gap-4 py-6">
      <aside className="space-y-3 min-w-0">
        {created && <ApiKeyBox created={created} onDismiss={() => setCreated(null)} />}
        <TenantList
          tenants={tenants.data} loading={tenants.loading} error={tenants.error}
          selected={selected} onSelect={select} verticals={verticals.data ?? []}
          verticalLabels={verticalLabels}
          onCreated={(c) => { setCreated(c); select(c.id); tenants.reload(); }}
        />
      </aside>

      <div className="space-y-4 min-w-0">
        {!tenant ? (
          <div className="rounded-xl border p-8 text-center" style={{ background: "var(--surface)", borderColor: "var(--line)" }}>
            <p className="mono text-[11px] tracking-[0.2em]" style={{ color: "var(--accent)" }}>NO TENANT SELECTED</p>
            <p className="text-sm mt-2" style={{ color: "var(--muted)" }}>
              {tenants.loading && !tenants.data ? "Connecting to the gateway..." : tenants.error && !tenants.data ? "Gateway not reachable yet. It may still be starting; retrying automatically." : "Create a tenant to get started."}
            </p>
          </div>
        ) : (
          <>
            <div>
              <p className="mono text-[11px] tracking-[0.2em]" style={{ color: vColor }}>{(verticalLabels[tenant.vertical] ?? tenant.vertical).toUpperCase()}</p>
              <h2 className="text-2xl font-bold break-words">{tenant.name || tenant.id}</h2>
              <p className="mono text-[11px]" style={{ color: "var(--muted)" }}>{tenant.id}{tenant.location ? ` · ${tenant.location}` : ""}</p>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
              <Stat label="Alerts 24h" value={tenant.alerts_24h} color={tenant.alerts_24h > 0 ? "var(--bad)" : "var(--accent)"} />
              <Stat label="Events 24h" value={tenant.events_24h} />
              <Stat label="Vertical critical 24h" value={vstats.data?.critical_24h ?? "-"} color="var(--bad)"
                sub={vstats.data ? `${vstats.data.alerts_24h} alerts · ${vstats.data.events_24h} events · ${vstats.data.tenants} tenants` : undefined} />
              <Stat label="Vertical levels" value={vstats.data ? `${vstats.data.by_level.NORMAL}/${vstats.data.by_level.ALERT}/${vstats.data.by_level.CRITICAL}` : "-"} color={vColor} sub="normal / alert / critical" />
              <Stat label="LLM calls today" value={llmTotal ?? "-"} color="var(--accent2)" sub={llmSub} />
            </div>
            <Notifications key={`n-${tenant.id}`} tenantId={tenant.id} />
            <LiveCameras key={tenant.id} tenantId={tenant.id} />
            <FeedSources key={`f-${tenant.id}`} tenantId={tenant.id} />
            <Inventory key={`i-${tenant.id}`} tenantId={tenant.id} />
            <AlertFeed alerts={alerts.data} loading={alerts.loading} error={alerts.error} reload={alerts.reload} />
            <div className="grid xl:grid-cols-2 gap-4">
              <EventTimeline tenantId={tenant.id} events={events.data} loading={events.loading} error={events.error} />
              <ActuationList items={acts.data} loading={acts.loading} error={acts.error} />
            </div>
            <div className="grid xl:grid-cols-2 gap-4">
              <TestFrame key={tenant.id} tenantId={tenant.id} vertical={tenant.vertical} />
              <KnowledgeGraph graph={graph.data} loading={graph.loading} error={graph.error} tenantId={tenant.id} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
