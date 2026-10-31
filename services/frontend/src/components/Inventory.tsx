import { useState, type FormEvent } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api, type InventoryItem } from "../api";
import { relTime, useNow, usePoll } from "../lib";
import { Empty, ErrorLine, Panel, Pill, btnGhost, btnPrimary, inputCls, inputStyle } from "./ui";

const STATUS_COLOR: Record<InventoryItem["status"], string> = { ok: "#34d399", low: "#fbbf24", expiring: "#fbbf24", expired: "#f87171" };

/** Stock on a fridge, medicine trolley or shelf. Counts come from what the camera sees; minimum stock and expiry are set here. */
export function Inventory({ tenantId }: { tenantId: string }) {
  const items = usePoll(() => api.inventory(tenantId), 10_000, [tenantId]);
  const now = useNow(15_000);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("fridge");
  const [min, setMin] = useState("1");
  const [expiry, setExpiry] = useState("");
  const [camera, setCamera] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      await api.saveInventory(tenantId, {
        name: name.trim(), kind, min_stock: Math.max(0, parseInt(min, 10) || 0),
        expiry: expiry || null, camera_id: camera.trim() || null,
      });
      setName(""); setExpiry(""); items.reload();
    } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    finally { setBusy(false); }
  };
  const remove = async (n: string) => {
    setBusy(true); setErr(null);
    try { await api.removeInventory(tenantId, n); items.reload(); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    finally { setBusy(false); }
  };

  const list = items.data ?? [];
  const attention = list.filter((i) => i.status !== "ok").length;
  return (
    <Panel kicker="STOCK" title="Fridge, medicine and shelf stock"
      right={<span className="mono text-[11px]" style={{ color: attention ? "#fbbf24" : "var(--muted)" }}>{attention} need attention</span>}>
      <form onSubmit={add} className="grid sm:grid-cols-[1fr_8rem_6rem_9rem_8rem_auto] gap-2">
        <input className={inputCls} style={inputStyle} placeholder="item (e.g. milk, paracetamol)" value={name} onChange={(e) => setName(e.target.value)} required maxLength={40} />
        <select className={inputCls} style={inputStyle} value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="fridge">fridge</option><option value="medicine">medicine</option><option value="general">general</option>
        </select>
        <input className={inputCls} style={inputStyle} type="number" min={0} title="Minimum stock" placeholder="min" value={min} onChange={(e) => setMin(e.target.value)} />
        <input className={inputCls} style={inputStyle} type="date" title="Expiry (optional)" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        <input className={inputCls} style={inputStyle} placeholder="camera (optional)" value={camera} onChange={(e) => setCamera(e.target.value)} maxLength={40} pattern="[A-Za-z0-9_\-]*" />
        <button disabled={busy} className="mono text-[11px] px-3 py-2 rounded-lg flex items-center justify-center gap-1.5 disabled:opacity-50" style={btnPrimary}><Plus size={12} />ADD</button>
      </form>
      <p className="text-[11px] mt-2" style={{ color: "var(--muted)" }}>
        The camera counts what it sees; a count only updates when a tracked item is in view, so a closed door does not read as empty. Counts are estimates: confirm before reordering.
      </p>
      <ErrorLine msg={err ?? items.error} />
      {list.length > 0 ? (
        <div className="overflow-x-auto mt-3">
          <table className="w-full text-left min-w-[560px]">
            <thead>
              <tr className="mono text-[10px] tracking-widest" style={{ color: "var(--muted)" }}>
                <th className="py-1.5 pr-3 font-normal">ITEM</th><th className="pr-3 font-normal">KIND</th><th className="pr-3 font-normal">SEEN / MIN</th>
                <th className="pr-3 font-normal">EXPIRY</th><th className="pr-3 font-normal">STATUS</th><th className="pr-3 font-normal">LAST SEEN</th><th />
              </tr>
            </thead>
            <tbody>
              {list.map((i) => (
                <tr key={i.id} className="border-t text-[12px]" style={{ borderColor: "var(--line)" }}>
                  <td className="py-1.5 pr-3 font-semibold">{i.name.replace(/_/g, " ")}</td>
                  <td className="mono pr-3" style={{ color: "var(--muted)" }}>{i.kind}</td>
                  <td className="mono pr-3">{i.count} / {i.min_stock}</td>
                  <td className="mono pr-3" style={{ color: "var(--muted)" }}>{i.expiry ?? "-"}</td>
                  <td className="pr-3"><Pill color={STATUS_COLOR[i.status]}>{i.status}</Pill></td>
                  <td className="mono pr-3" style={{ color: "var(--muted)" }}>{i.last_seen ? relTime(i.last_seen, now) : "not seen yet"}</td>
                  <td className="text-right"><button disabled={busy} onClick={() => remove(i.name)} aria-label={`Remove ${i.name}`} className="p-1.5 rounded-lg border disabled:opacity-50" style={btnGhost}><Trash2 size={12} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="mt-3"><Empty>{items.loading && !items.data ? "Loading..." : "No items tracked yet. Add milk, eggs, paracetamol... and set a minimum."}</Empty></div>
      )}
    </Panel>
  );
}
