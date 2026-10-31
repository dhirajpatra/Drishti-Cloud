import { useState, type FormEvent } from "react";
import { ChevronDown, ChevronRight, Copy, Plus, X } from "lucide-react";
import { api, type CreatedTenant, type Tenant, type Vertical, type VerticalId } from "../api";
import { relTime, useNow, verticalColor } from "../lib";
import { Empty, ErrorLine, Pill, btnGhost, btnPrimary, inputCls, inputStyle } from "./ui";

const ID_RE = /^[a-z0-9][a-z0-9_-]{1,62}$/;

function AddTenant({ verticals, onCreated }: { verticals: Vertical[]; onCreated: (t: CreatedTenant) => void }) {
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [vertical, setVertical] = useState<VerticalId | "">("");
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const v = vertical || verticals[0]?.id || "";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    if (!ID_RE.test(id)) { setErr("id must match ^[a-z0-9][a-z0-9_-]{1,62}$"); return; }
    if (!v) { setErr("No verticals available"); return; }
    setBusy(true);
    try {
      const created = await api.createTenant({ id, vertical: v, name: name.trim() || undefined, location: location.trim() || undefined });
      onCreated(created);
      setId(""); setName(""); setLocation(""); setOpen(false);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally { setBusy(false); }
  }

  return (
    <div className="rounded-xl border" style={{ background: "var(--surface)", borderColor: "var(--line)" }}>
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-4 py-3 mono text-[11px] tracking-widest" style={{ color: "var(--accent)" }}>
        <span className="flex items-center gap-2"><Plus size={14} /> ADD TENANT</span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <form onSubmit={submit} className="px-4 pb-4 space-y-2">
          <input className={inputCls} style={inputStyle} placeholder="id (e.g. mall-01)" value={id} onChange={(e) => setId(e.target.value)} />
          <select className={inputCls} style={inputStyle} value={v} onChange={(e) => setVertical(e.target.value as VerticalId)}>
            {verticals.length === 0 && <option value="">loading verticals...</option>}
            {verticals.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
          </select>
          <input className={inputCls} style={inputStyle} placeholder="name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
          <input className={inputCls} style={inputStyle} placeholder="location (optional)" value={location} onChange={(e) => setLocation(e.target.value)} />
          <ErrorLine msg={err} />
          <button disabled={busy} className="mono text-[12px] px-4 py-2 rounded-lg disabled:opacity-50 w-full" style={btnPrimary}>{busy ? "CREATING..." : "CREATE TENANT"}</button>
        </form>
      )}
    </div>
  );
}

export function ApiKeyBox({ created, onDismiss }: { created: CreatedTenant; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(created.api_key); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  return (
    <div className="rounded-xl border p-3" style={{ borderColor: "var(--warn)", background: "var(--warn-bg)" }}>
      <div className="flex items-start justify-between gap-2">
        <p className="mono text-[11px] tracking-widest" style={{ color: "var(--warn)" }}>API KEY FOR {created.id} - SHOWN ONCE</p>
        <button onClick={onDismiss} aria-label="Dismiss" style={{ color: "var(--muted)" }}><X size={14} /></button>
      </div>
      <div className="flex items-center gap-2 mt-2">
        <code className="mono text-[11px] flex-1 break-all rounded px-2 py-1" style={{ background: "var(--field)", color: "var(--ink2)" }}>{created.api_key}</code>
        <button onClick={copy} className="mono text-[10px] flex items-center gap-1 px-2 py-1 rounded border shrink-0" style={btnGhost}><Copy size={11} />{copied ? "COPIED" : "COPY"}</button>
      </div>
      <p className="text-[11px] mt-2" style={{ color: "var(--muted)" }}>Store it now; it cannot be retrieved later.</p>
    </div>
  );
}

export function TenantList({ tenants, loading, error, selected, onSelect, verticals, onCreated, verticalLabels }: {
  tenants: Tenant[] | undefined; loading: boolean; error: string | null; selected: string | null;
  onSelect: (id: string) => void; verticals: Vertical[]; onCreated: (t: CreatedTenant) => void;
  verticalLabels: Record<string, string>;
}) {
  const now = useNow(5000);
  return (
    <div className="space-y-3">
      <p className="mono text-[11px] tracking-[0.2em]" style={{ color: "var(--accent)" }}>TENANTS{tenants ? ` · ${tenants.length}` : ""}</p>
      <ErrorLine msg={error} />
      {loading && !tenants && <Empty>Loading tenants...</Empty>}
      {tenants && tenants.length === 0 && <Empty>No tenants yet. Add one below.</Empty>}
      {tenants?.map((t) => {
        const c = verticalColor(t.vertical);
        const active = t.id === selected;
        return (
          <button key={t.id} onClick={() => onSelect(t.id)} className="w-full text-left rounded-xl border p-3 transition block"
            style={{ background: active ? "var(--surface2)" : "var(--surface)", borderColor: active ? "var(--accent)" : "var(--line)" }}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-semibold truncate flex items-center gap-2">
                  {t.alerts_24h > 0 && <span className="w-2 h-2 rounded-full shrink-0" style={{ background: "var(--bad)" }} title="alerts in last 24h" />}
                  <span className="truncate">{t.name || t.id}</span>
                </p>
                <p className="mono text-[10px] truncate" style={{ color: "var(--muted)" }}>{t.id}{t.location ? ` · ${t.location}` : ""}</p>
              </div>
              <Pill color={c}>{verticalLabels[t.vertical] ?? t.vertical}</Pill>
            </div>
            <div className="flex gap-4 mt-2 mono text-[11px]" style={{ color: "var(--muted)" }}>
              <span><b style={{ color: t.alerts_24h > 0 ? "var(--bad)" : "var(--ink)" }}>{t.alerts_24h}</b> alerts</span>
              <span><b style={{ color: "var(--ink)" }}>{t.events_24h}</b> events</span>
              <span className="ml-auto">{relTime(t.last_seen, now)}</span>
            </div>
          </button>
        );
      })}
      <AddTenant verticals={verticals} onCreated={onCreated} />
    </div>
  );
}
