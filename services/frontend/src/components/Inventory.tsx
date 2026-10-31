import { useState, type FormEvent } from "react";
import { Plus, ScanSearch, ShoppingBasket, Trash2 } from "lucide-react";
import { api, type InventoryItem } from "../api";
import { relTime, useNow, usePoll } from "../lib";
import { Empty, ErrorLine, Panel, Pill, btnGhost, btnPrimary, inputCls, inputStyle } from "./ui";

const STATUS_COLOR: Record<InventoryItem["status"], string> = {
  ok: "var(--ok)",
  low: "var(--warn)",
  expiring: "var(--warn)",
  expired: "var(--bad)"
};

/** Stock on a fridge, medicine trolley or shelf. Counts come from what the camera sees; minimum stock and expiry are set here. */
export function Inventory({ tenantId }: { tenantId: string }) {
  const items = usePoll(() => api.inventory(tenantId), 10_000, [tenantId]);
  const shop = usePoll(() => api.shopping(tenantId), 10_000, [tenantId]);
  const cams = usePoll(() => api.cameras(tenantId), 30_000, [tenantId]);
  const [fridgeCam, setFridgeCam] = useState<string>("");
  const [info, setInfo] = useState<string | null>(null);
  const now = useNow(15_000);
  const [name, setName] = useState<string>("");
  const [kind, setKind] = useState<string>("fridge");
  const [min, setMin] = useState<string>("1");
  const [expiry, setExpiry] = useState<string>("");
  const [camera, setCamera] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api.saveInventory(tenantId, {
        name: name.trim(),
        kind,
        min_stock: Math.max(0, parseInt(min, 10) || 0),
        expiry: expiry || null,
        camera_id: camera.trim() || null,
      });
      setName("");
      setExpiry("");
      items.reload();
    } catch (x) {
      setErr(x instanceof Error ? x.message : String(x));
    } finally {
      setBusy(false);
    }
  };

  const cam = fridgeCam || cams.data?.[0]?.camera_id || "";
  const staples = async () => {
    setBusy(true); setErr(null); setInfo(null);
    try {
      const r = await api.addStaples(tenantId, cam);
      setInfo(r.added.length ? `Now tracking: ${r.added.join(", ")}. Looking at the fridge...` : "Everyday food is already tracked. Looking at the fridge...");
      items.reload(); shop.reload();
    } catch (x) { setErr(x instanceof Error ? x.message : String(x)); } finally { setBusy(false); }
  };
  const scan = async () => {
    setBusy(true); setErr(null); setInfo("Looking inside the fridge...");
    try {
      await api.scanFridge(tenantId, cam);
      for (let i = 0; i < 12; i++) {                       // the fridge agent answers within ~10-30 s
        await new Promise((r) => setTimeout(r, 2500));
        const r = await api.scanResult(tenantId, cam);
        if (r.result) { setInfo(r.result.startsWith("updated") ? "Fridge checked. " + r.result : r.result); break; }
      }
      items.reload(); shop.reload();
    } catch (x) { setErr(x instanceof Error ? x.message : String(x)); } finally { setBusy(false); }
  };

  const remove = async (itemIdentifier: string) => {
    setBusy(true);
    setErr(null);
    try {
      await api.removeInventory(tenantId, itemIdentifier);
      items.reload();
    } catch (x) {
      setErr(x instanceof Error ? x.message : String(x));
    } finally {
      setBusy(false);
    }
  };

  const list: InventoryItem[] = Array.isArray(items.data) ? items.data : [];
  const attention = list.filter((i: InventoryItem) => i.status !== "ok").length;

  return (
    <Panel
      kicker="STOCK"
      title="Fridge, medicine and shelf stock"
      right={
        <span
          className="mono text-[11px]"
          style={{ color: attention ? "var(--warn)" : "var(--muted)" }}
        >
          {attention} need attention
        </span>
      }
    >
      <div className="rounded-xl border p-3 mb-3" style={{ borderColor: shop.data?.to_buy.length ? "var(--warn)" : "var(--line)", background: shop.data?.to_buy.length ? "var(--warn-bg)" : "transparent" }}>
        <p className="mono text-[11px] tracking-widest flex items-center gap-1.5" style={{ color: shop.data?.to_buy.length ? "var(--warn)" : "var(--muted)" }}>
          <ShoppingBasket size={13} />NEED TO BUY
        </p>
        {shop.data?.to_buy.length ? (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {shop.data.to_buy.map((i) => (
              <span key={i.id} className="text-[12px] font-semibold px-2.5 py-1 rounded-full border" style={{ borderColor: "var(--warn)" }}>
                {i.name.replace(/_/g, " ")} <span className="mono font-normal" style={{ color: "var(--muted)" }}>{i.count === 0 ? "none seen" : `${i.count} of ${i.min_stock}`}</span>
              </span>))}
          </div>
        ) : (
          <p className="text-[12px] mt-1" style={{ color: "var(--muted)" }}>
            {shop.data && shop.data.unchecked.length ? `Nothing is missing so far. ${shop.data.unchecked.length} item(s) not checked yet: open the fridge door in front of the camera, or press Scan.` : "Nothing is missing."}
          </p>)}
        <div className="flex flex-wrap items-center gap-2 mt-3">
          <select className={inputCls + " !w-auto"} style={inputStyle} value={cam} onChange={(e) => setFridgeCam(e.target.value)} aria-label="Fridge camera">
            {(cams.data ?? []).map((c) => <option key={c.camera_id} value={c.camera_id}>{c.camera_id}</option>)}
          </select>
          <button disabled={busy || !cam} onClick={staples} className="mono text-[11px] px-3 py-2 rounded-lg flex items-center gap-1.5 disabled:opacity-50" style={btnPrimary}><Plus size={12} />TRACK EVERYDAY FOOD</button>
          <button disabled={busy || !cam} onClick={scan} className="mono text-[11px] px-3 py-2 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={btnGhost}><ScanSearch size={12} />SCAN FRIDGE NOW</button>
        </div>
        <p className="text-[11px] mt-2" style={{ color: "var(--muted)" }}>Milk, eggs, bread, butter, cheese, curd, vegetables, fruits, juice. The camera must see <b>inside</b> the fridge; a closed door changes nothing.</p>
        {info && <p className="mono text-[11px] mt-1" style={{ color: "var(--ok)" }}>{info}</p>}
      </div>
      <form onSubmit={add} className="grid sm:grid-cols-[1fr_8rem_6rem_9rem_8rem_auto] gap-2">
        <input
          className={inputCls}
          style={inputStyle}
          placeholder="item (e.g. milk, paracetamol)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          maxLength={40}
        />
        <select
          className={inputCls}
          style={inputStyle}
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          <option value="fridge">fridge</option>
          <option value="medicine">medicine</option>
          <option value="general">general</option>
        </select>
        <input
          className={inputCls}
          style={inputStyle}
          type="number"
          min={0}
          title="Minimum stock"
          placeholder="min"
          value={min}
          onChange={(e) => setMin(e.target.value)}
        />
        <input
          className={inputCls}
          style={inputStyle}
          type="date"
          title="Expiry (optional)"
          value={expiry}
          onChange={(e) => setExpiry(e.target.value)}
        />
        <input
          className={inputCls}
          style={inputStyle}
          placeholder="camera (optional)"
          value={camera}
          onChange={(e) => setCamera(e.target.value)}
          maxLength={40}
          pattern="[A-Za-z0-9_\-]*"
        />
        <button
          disabled={busy}
          className="mono text-[11px] px-3 py-2 rounded-lg flex items-center justify-center gap-1.5 disabled:opacity-50"
          style={btnPrimary}
        >
          <Plus size={12} />
          ADD
        </button>
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
                <th className="py-1.5 pr-3 font-normal">ITEM</th>
                <th className="pr-3 font-normal">KIND</th>
                <th className="pr-3 font-normal">SEEN / MIN</th>
                <th className="pr-3 font-normal">EXPIRY</th>
                <th className="pr-3 font-normal">STATUS</th>
                <th className="pr-3 font-normal">LAST SEEN</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((i: InventoryItem) => (
                <tr key={i.id} className="border-t text-[12px]" style={{ borderColor: "var(--line)" }}>
                  <td className="py-1.5 pr-3 font-semibold">{i.name.replace(/_/g, " ")}</td>
                  <td className="mono pr-3" style={{ color: "var(--muted)" }}>{i.kind}</td>
                  <td className="mono pr-3">{i.count} / {i.min_stock}</td>
                  <td className="mono pr-3" style={{ color: "var(--muted)" }}>{i.expiry ?? "-"}</td>
                  <td className="pr-3">
                    <Pill color={STATUS_COLOR[i.status]}>{i.status}</Pill>
                  </td>
                  <td className="mono pr-3" style={{ color: "var(--muted)" }}>
                    {i.last_seen ? relTime(i.last_seen, now) : "not seen yet"}
                  </td>
                  <td className="text-right">
                    <button
                      disabled={busy}
                      onClick={() => remove(i.name)}
                      aria-label={`Remove ${i.name}`}
                      className="p-1.5 rounded-lg border disabled:opacity-50"
                      style={btnGhost}
                    >
                      <Trash2 size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="mt-3">
          <Empty>
            {items.loading && !items.data
              ? "Loading..."
              : "No items tracked yet. Add milk, eggs, paracetamol... and set a minimum."}
          </Empty>
        </div>
      )}
    </Panel>
  );
}