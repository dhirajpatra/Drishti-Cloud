import { useState, type FormEvent } from "react";
import { Send } from "lucide-react";
import { api, type VerticalId } from "../api";
import { SENSOR_PRESETS } from "../lib";
import { ErrorLine, Panel, btnGhost, btnPrimary, inputCls, inputStyle } from "./ui";

const PRESET_LABEL: Record<VerticalId, string> = { mall_security: "mall", home_seniors: "home_seniors", school_children: "school_children", icu_hospital: "icu_hospital" };

export function TestFrame({ tenantId, vertical }: { tenantId: string; vertical: VerticalId }) {
  const [file, setFile] = useState<File | null>(null);
  const [camera, setCamera] = useState("cam_1");
  const [sensors, setSensors] = useState(SENSOR_PRESETS[vertical]);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null); setResult(null);
    if (!file) { setErr("Choose a JPEG or PNG image first"); return; }
    try { JSON.parse(sensors); } catch (x) { setErr(`Sensors JSON is invalid: ${x instanceof Error ? x.message : String(x)}`); return; }
    setBusy(true);
    try {
      const r = await api.ingest(tenantId, file, sensors, camera.trim(), force);
      setResult(JSON.stringify(r, null, 2));
    } catch (x) {
      setErr(x instanceof Error ? x.message : String(x));
    } finally { setBusy(false); }
  }

  return (
    <Panel kicker="INGEST" title="Send test frame">
      <form onSubmit={submit} className="space-y-3">
        <div className="grid sm:grid-cols-2 gap-3">
          <label className="block">
            <span className="mono text-[10px] tracking-widest" style={{ color: "var(--muted)" }}>IMAGE (JPEG/PNG)</span>
            <input type="file" accept="image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="mt-1 block w-full text-[12px] file:mr-3 file:rounded-lg file:border file:px-3 file:py-1.5 file:mono file:text-[11px] file:bg-transparent" style={{ color: "var(--muted)" }} />
          </label>
          <label className="block">
            <span className="mono text-[10px] tracking-widest" style={{ color: "var(--muted)" }}>CAMERA ID</span>
            <input className={`${inputCls} mt-1`} style={inputStyle} value={camera} onChange={(e) => setCamera(e.target.value)} />
          </label>
        </div>
        <div>
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="mono text-[10px] tracking-widest" style={{ color: "var(--muted)" }}>SENSORS JSON</span>
            {(Object.keys(SENSOR_PRESETS) as VerticalId[]).map((v) => (
              <button type="button" key={v} onClick={() => setSensors(SENSOR_PRESETS[v])} className="mono text-[10px] px-2 py-0.5 rounded border" style={btnGhost}>{PRESET_LABEL[v]}</button>
            ))}
          </div>
          <textarea rows={3} className={`${inputCls} mono text-[12px]`} style={inputStyle} value={sensors} onChange={(e) => setSensors(e.target.value)} spellCheck={false} />
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />Force (skip static-frame gate)</label>
          <button disabled={busy} className="mono text-[12px] px-4 py-2 rounded-lg flex items-center gap-2 disabled:opacity-50 ml-auto" style={btnPrimary}><Send size={13} />{busy ? "SENDING..." : "SEND FRAME"}</button>
        </div>
        <ErrorLine msg={err} />
        {result && <pre className="mono text-[11px] rounded-lg p-3 overflow-x-auto" style={{ background: "var(--field)", color: "var(--ink2)" }}>{result}</pre>}
      </form>
    </Panel>
  );
}
