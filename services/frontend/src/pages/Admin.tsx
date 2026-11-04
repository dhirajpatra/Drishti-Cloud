import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Camera, Check, Plus, RotateCcw, Save, ShieldAlert, Trash2, Wifi } from "lucide-react";
import { ConnectCamera } from "../components/ConnectCamera";
import { api, type AdminConfig, type AdminOverview, type AdminTenant, type AlertLevel, type CameraConn, type CameraForm, type CustomRule, type LabelRule, type SiteCfg, type TestResult } from "../api";
import { usePoll } from "../lib";
import { Chip, Empty, ErrorLine, Panel, Pill, btnGhost, btnPrimary, inputCls, inputStyle } from "../components/ui";

const msg = (x: unknown) => (x instanceof Error ? x.message : String(x));
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
const LEVEL_COLOR: Record<string, string> = { ALERT: "var(--warn)", CRITICAL: "var(--bad)" };

function Field({ label, hint, changed, children }: { label: string; hint?: string; changed?: boolean; children: ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="mono text-[10px] tracking-widest uppercase flex items-center gap-1.5" style={{ color: "var(--muted)" }}>
        {label}{changed && <span style={{ color: "var(--accent)" }}>● CUSTOM</span>}
      </span>
      {children}
      {hint && <span className="text-[11px] block mt-0.5" style={{ color: "var(--muted)" }}>{hint}</span>}
    </label>
  );
}

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      className="mono text-[10px] px-2 py-1 rounded-full border shrink-0" style={{ borderColor: on ? "var(--ok)" : "var(--line)", color: on ? "var(--ok)" : "var(--muted)" }}>
      {on ? "ON" : "OFF"}
    </button>
  );
}

function LevelSelect({ value, onChange, auto }: { value: AlertLevel | null; onChange: (v: AlertLevel | null) => void; auto?: boolean }) {
  return (
    <select className={inputCls} style={inputStyle} value={value ?? ""} onChange={(e) => onChange((e.target.value || null) as AlertLevel | null)}>
      {auto && <option value="">Automatic</option>}
      <option value="ALERT">ALERT</option><option value="CRITICAL">CRITICAL</option>
    </select>
  );
}

function Hours({ value, onChange }: { value: [number, number] | null; onChange: (v: [number, number] | null) => void }) {
  const [a, b] = value ?? ["", ""];
  const set = (x: string, y: string) => onChange(x === "" || y === "" ? null : [Number(x), Number(y)]);
  return (
    <div className="flex items-center gap-1">
      <input className={inputCls} style={inputStyle} type="number" min={0} max={23} placeholder="from" value={a} onChange={(e) => set(e.target.value, String(b))} />
      <span style={{ color: "var(--muted)" }}>–</span>
      <input className={inputCls} style={inputStyle} type="number" min={0} max={23} placeholder="to" value={b} onChange={(e) => set(String(a), e.target.value)} />
    </div>
  );
}

const num = (v: string, fallback: number) => (v === "" || Number.isNaN(Number(v)) ? fallback : Number(v));

/* ------------------------------------------------------------------ connection */
function Connection({ tenant, camera, reload }: { tenant: string; camera: string; reload: () => void }) {
  const [cur, setCur] = useState<CameraConn | null>(null);
  const [f, setF] = useState<CameraForm>({ scheme: "http", host: "", port: null, path: "/", username: "", enabled: true });
  const [pw, setPw] = useState("");
  const [clearPw, setClearPw] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setCur(null); setPw(""); setClearPw(false); setErr(null); setOk(null); setTest(null);
    api.adminCamera(tenant, camera).then((c) => {
      if (!alive) return;
      setCur(c); setF({ scheme: c.scheme, host: c.host, port: c.port, path: c.path, username: c.username, enabled: c.enabled });
    }).catch((x) => alive && setErr(msg(x)));
    return () => { alive = false; };
  }, [tenant, camera]);

  const body = (): CameraForm => ({ ...f, ...(clearPw ? { clear_password: true } : pw ? { password: pw } : {}) });
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true); setErr(null); setOk(null);
    try { await fn(); if (done) setOk(done); } catch (x) { setErr(msg(x)); } finally { setBusy(false); }
  };
  const save = (e: FormEvent) => { e.preventDefault(); void run(async () => {
    const c = await api.saveAdminCamera(tenant, camera, body()); setCur(c); setPw(""); setClearPw(false); reload();
  }, "Saved. The feed-ingestor uses the new address on its next sample (within seconds)."); };
  const doTest = () => void run(async () => { setTest(null); setTest(await api.testAdminCamera(tenant, camera, body())); });
  const remove = () => void run(async () => { await api.deleteAdminCamera(tenant, camera); reload(); setCur(null); }, "Saved connection removed.");
  const file = f.scheme === "file";

  if (!cur && !err) return <Empty>Loading...</Empty>;
  return (
    <form onSubmit={save} className="space-y-3">
      {cur?.source === "env" && <p className="text-[12px]" style={{ color: "var(--warn)" }}>This camera comes from <span className="mono">RTSP_FEEDS</span> in .env. Saving here moves it to the database (its current password is kept), and the saved address wins from then on.</p>}
      {cur?.source === "edge" && <p className="text-[12px]" style={{ color: "var(--muted)" }}>No connection saved. Frames for this camera currently arrive from an edge device or manual upload. Fill this in to have Drishti pull from a camera.</p>}
      <div className="grid sm:grid-cols-[8rem_1fr_7rem] gap-2">
        <Field label="Protocol"><select className={inputCls} style={inputStyle} value={f.scheme} onChange={(e) => setF({ ...f, scheme: e.target.value })}>
          {["http", "https", "rtsp", "rtsps", "file"].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="IP address or host" hint="Change this when your router hands the camera a new internal IP.">
          <input className={inputCls} style={inputStyle} disabled={file} placeholder="192.168.1.4" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value.trim() })} /></Field>
        <Field label="Port"><input className={inputCls} style={inputStyle} disabled={file} type="number" min={1} max={65535} placeholder="8081" value={f.port ?? ""} onChange={(e) => setF({ ...f, port: e.target.value ? Number(e.target.value) : null })} /></Field>
      </div>
      <Field label={file ? "Video file (under ./feeds)" : "Path"} hint="motionEye snapshot: /picture/1/current/ · RTSP: /stream1 · ESP32-CAM: /capture">
        <input className={inputCls} style={inputStyle} value={f.path} onChange={(e) => setF({ ...f, path: e.target.value })} /></Field>
      <div className="grid sm:grid-cols-2 gap-2">
        <Field label="Camera username"><input className={inputCls} style={inputStyle} autoComplete="off" disabled={file} value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="Camera password" hint={cur?.has_password && !clearPw ? "A password is stored (encrypted). Leave empty to keep it." : "Stored encrypted. Never shown again."}>
          <input className={inputCls} style={inputStyle} type="password" autoComplete="new-password" disabled={file || clearPw} placeholder={cur?.has_password ? "••••••••  (unchanged)" : ""} value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
      </div>
      {cur?.has_password && <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--muted)" }}><input type="checkbox" checked={clearPw} onChange={(e) => setClearPw(e.target.checked)} />Remove the stored password (camera needs none)</label>}
      <div className="flex items-center gap-2"><Toggle on={f.enabled} onChange={(v) => setF({ ...f, enabled: v })} label="camera enabled" /><span className="text-[12px]" style={{ color: "var(--muted)" }}>{f.enabled ? "Camera is sampled" : "Paused: Drishti will not pull from this camera"}</span></div>
      <div className="flex flex-wrap gap-2">
        <button disabled={busy} className="mono text-[11px] px-3 py-2 rounded-lg flex items-center gap-1.5 disabled:opacity-50" style={btnPrimary}><Save size={12} />SAVE</button>
        <button type="button" disabled={busy} onClick={doTest} className="mono text-[11px] px-3 py-2 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={btnGhost}><Wifi size={12} />TEST CONNECTION</button>
        {cur?.source === "database" && <button type="button" disabled={busy} onClick={remove} className="mono text-[11px] px-3 py-2 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={btnGhost}><Trash2 size={12} />REMOVE SAVED</button>}
      </div>
      <ErrorLine msg={err} />
      {ok && <p className="mono text-[11px]" style={{ color: "var(--ok)" }}>✓ {ok}</p>}
      {test && (test.ok
        ? <div><p className="mono text-[11px] mb-1" style={{ color: "var(--ok)" }}>✓ Connected, picture received ({Math.round(test.bytes / 1024)} KB)</p>{test.preview && <img src={test.preview} alt="Camera preview" className="rounded-lg border max-h-56" style={{ borderColor: "var(--line)" }} />}</div>
        : <p className="mono text-[11px]" style={{ color: "var(--bad)" }}>✗ Could not get a picture: {test.error}</p>)}
      {test?.stages && test.stages.length > 0 && (
        <ol className="space-y-0.5">
          {test.stages.map((st) => (
            <li key={st.stage} className="text-[11px]">
              <span className="mono" style={{ color: st.ok ? "var(--ok)" : "var(--bad)" }}>{st.ok ? "✓" : "✗"} {st.stage}</span>
              <span style={{ color: "var(--muted)" }}> {st.detail}</span>
              {!st.ok && st.hint && <p className="ml-4" style={{ color: "var(--warn)" }}>→ {st.hint}</p>}
            </li>))}
        </ol>)}
    </form>
  );
}

/* ------------------------------------------------------------------ alarms */
function Alarms({ cfg, defaults, cat, set }: { cfg: SiteCfg; defaults: SiteCfg; cat: AdminConfig["catalog"]; set: (c: SiteCfg) => void }) {
  const patchRule = (id: string, p: Partial<SiteCfg["rules"][string]>) => set({ ...cfg, rules: { ...cfg.rules, [id]: { ...cfg.rules[id], ...p } } });
  return (
    <div className="space-y-4">
      <div>
        <p className="mono text-[11px] tracking-widest mb-2" style={{ color: "var(--accent)" }}>WHICH ALARMS, HOW LOUD, WHEN</p>
        <ul className="space-y-2">
          {cat.rules.map((r) => {
            const c = cfg.rules[r.id]; const custom = !same(c, defaults.rules[r.id]);
            return (
              <li key={r.id} className="rounded-lg border p-2.5" style={{ borderColor: custom ? "var(--accent)" : "var(--line)", opacity: c.enabled ? 1 : 0.6 }}>
                <div className="flex items-start gap-2">
                  <Toggle on={c.enabled} onChange={(v) => patchRule(r.id, { enabled: v })} label={`${r.label} enabled`} />
                  <div className="min-w-0 flex-1"><p className="text-sm font-semibold">{r.label}</p><p className="text-[11px]" style={{ color: "var(--muted)" }}>{r.description}</p></div>
                  <Chip color={LEVEL_COLOR[r.default_level]}>default {r.default_level}</Chip>
                </div>
                {c.enabled && (
                  <div className="grid sm:grid-cols-3 gap-2 mt-2">
                    <Field label="Level"><LevelSelect auto value={c.level} onChange={(v) => patchRule(r.id, { level: v })} /></Field>
                    <Field label="Repeat no sooner than (sec)" hint="empty = global cooldown"><input className={inputCls} style={inputStyle} type="number" min={0} max={86400} value={c.cooldown_sec ?? ""} onChange={(e) => patchRule(r.id, { cooldown_sec: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
                    <Field label="Only between hours" hint="empty = always (e.g. 22 – 6)"><Hours value={c.hours} onChange={(v) => patchRule(r.id, { hours: v })} /></Field>
                  </div>)}
              </li>);
          })}
        </ul>
      </div>
      {cat.thresholds.length > 0 && (
        <div>
          <p className="mono text-[11px] tracking-widest mb-2" style={{ color: "var(--accent)" }}>LIMITS</p>
          <div className="grid sm:grid-cols-2 gap-3">
            {cat.thresholds.map((t) => (
              <Field key={t.key} label={`${t.label} (${t.unit})`} hint={`default ${defaults.thresholds[t.key]} · allowed ${t.min}–${t.max}`} changed={cfg.thresholds[t.key] !== defaults.thresholds[t.key]}>
                <input className={inputCls} style={inputStyle} type="number" min={t.min} max={t.max} value={cfg.thresholds[t.key] ?? ""} onChange={(e) => set({ ...cfg, thresholds: { ...cfg.thresholds, [t.key]: num(e.target.value, defaults.thresholds[t.key]) } })} /></Field>))}
          </div>
        </div>)}
    </div>
  );
}

/* ------------------------------------------------------------------ custom rules */
function Custom({ cfg, set }: { cfg: SiteCfg; set: (c: SiteCfg) => void }) {
  const nextId = (p: string, rows: { id: string }[]) => { let i = rows.length + 1; while (rows.some((r) => r.id === `${p}${i}`)) i++; return `${p}${i}`; };
  const cr = cfg.custom_rules, lr = cfg.label_rules;
  const setC = (i: number, p: Partial<CustomRule>) => set({ ...cfg, custom_rules: cr.map((r, k) => (k === i ? { ...r, ...p } : r)) });
  const setL = (i: number, p: Partial<LabelRule>) => set({ ...cfg, label_rules: lr.map((r, k) => (k === i ? { ...r, ...p } : r)) });
  return (
    <div className="space-y-5">
      <div>
        <p className="mono text-[11px] tracking-widest" style={{ color: "var(--accent)" }}>WATCH FOR (PLAIN ENGLISH)</p>
        <p className="text-[11px] mb-2" style={{ color: "var(--muted)" }}>Describe anything in your own words. The vision model checks it on each analysed picture. Example: “Anyone climbing the compound wall”, “Stove on with the pot boiling over”.</p>
        <ul className="space-y-2">
          {cr.map((r, i) => (
            <li key={r.id} className="rounded-lg border p-2.5 space-y-2" style={{ borderColor: "var(--line)" }}>
              <div className="flex items-center gap-2">
                <Toggle on={r.enabled} onChange={(v) => setC(i, { enabled: v })} label="rule enabled" />
                <input className={inputCls} style={inputStyle} maxLength={300} placeholder="What should Drishti watch for?" value={r.text} onChange={(e) => setC(i, { text: e.target.value })} />
                <button type="button" aria-label="Delete rule" className="p-1.5 rounded-lg border" style={btnGhost} onClick={() => set({ ...cfg, custom_rules: cr.filter((_, k) => k !== i) })}><Trash2 size={12} /></button>
              </div>
              <div className="grid grid-cols-2 gap-2"><Field label="Level"><LevelSelect value={r.level} onChange={(v) => setC(i, { level: v ?? "ALERT" })} /></Field>
                <Field label="Only between hours"><Hours value={r.hours} onChange={(v) => setC(i, { hours: v })} /></Field></div>
            </li>))}
        </ul>
        <button type="button" disabled={cr.length >= 20} className="mono text-[11px] px-3 py-1.5 rounded-lg border mt-2 flex items-center gap-1.5" style={btnGhost}
          onClick={() => set({ ...cfg, custom_rules: [...cr, { id: nextId("c", cr), text: "", level: "ALERT", enabled: true, hours: null }] })}><Plus size={12} />ADD A RULE</button>
      </div>
      <div>
        <p className="mono text-[11px] tracking-widest" style={{ color: "var(--accent)" }}>OBJECT RULES (EXACT, NO AI GUESSING)</p>
        <p className="text-[11px] mb-2" style={{ color: "var(--muted)" }}>Fires from detected objects. Example: “person absent for 600 s → Counter unattended”, “bag present for 120 s”, “person count ≥ 10”.</p>
        <ul className="space-y-2">
          {lr.map((r, i) => (
            <li key={r.id} className="rounded-lg border p-2.5 space-y-2" style={{ borderColor: "var(--line)" }}>
              <div className="flex items-center gap-2">
                <Toggle on={r.enabled} onChange={(v) => setL(i, { enabled: v })} label="rule enabled" />
                <input className={inputCls} style={inputStyle} maxLength={200} placeholder="Message shown in the alert" value={r.message} onChange={(e) => setL(i, { message: e.target.value })} />
                <button type="button" aria-label="Delete rule" className="p-1.5 rounded-lg border" style={btnGhost} onClick={() => set({ ...cfg, label_rules: lr.filter((_, k) => k !== i) })}><Trash2 size={12} /></button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                <Field label="Object"><input className={inputCls} style={inputStyle} placeholder="person" value={r.label} onChange={(e) => setL(i, { label: e.target.value.toLowerCase().replace(/[^a-z0-9_ ]/g, "") })} /></Field>
                <Field label="Condition"><select className={inputCls} style={inputStyle} value={r.op} onChange={(e) => setL(i, { op: e.target.value as LabelRule["op"] })}>
                  <option value="present">is present</option><option value="absent">is absent</option><option value="count_gte">count ≥</option></select></Field>
                <Field label="Count"><input className={inputCls} style={inputStyle} type="number" min={1} disabled={r.op !== "count_gte"} value={r.value} onChange={(e) => setL(i, { value: num(e.target.value, 1) })} /></Field>
                <Field label="For (sec)"><input className={inputCls} style={inputStyle} type="number" min={0} value={r.for_sec} onChange={(e) => setL(i, { for_sec: num(e.target.value, 0) })} /></Field>
                <Field label="Level"><LevelSelect value={r.level} onChange={(v) => setL(i, { level: v ?? "ALERT" })} /></Field>
              </div>
              <Field label="Only between hours"><Hours value={r.hours} onChange={(v) => setL(i, { hours: v })} /></Field>
            </li>))}
        </ul>
        <button type="button" disabled={lr.length >= 20} className="mono text-[11px] px-3 py-1.5 rounded-lg border mt-2 flex items-center gap-1.5" style={btnGhost}
          onClick={() => set({ ...cfg, label_rules: [...lr, { id: nextId("o", lr), label: "person", op: "present", value: 1, for_sec: 0, level: "ALERT", message: "", enabled: true, hours: null }] })}><Plus size={12} />ADD AN OBJECT RULE</button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ notifications + sampling */
function Notify({ cfg, defaults, cat, ov, set }: { cfg: SiteCfg; defaults: SiteCfg; cat: AdminConfig["catalog"]; ov: AdminOverview | undefined; set: (c: SiteCfg) => void }) {
  const n = cfg.notify; const setN = (p: Partial<SiteCfg["notify"]>) => set({ ...cfg, notify: { ...n, ...p } });
  const toggleKind = (lvl: AlertLevel, k: string) => setN({ policy: { ...n.policy, [lvl]: n.policy[lvl].includes(k) ? n.policy[lvl].filter((x) => x !== k) : [...n.policy[lvl], k] } });
  return (
    <div className="space-y-4">
      {(["ALERT", "CRITICAL"] as AlertLevel[]).map((lvl) => (
        <div key={lvl}>
          <p className="mono text-[11px] tracking-widest mb-1.5" style={{ color: LEVEL_COLOR[lvl] }}>WHEN {lvl}: DO</p>
          <div className="flex flex-wrap gap-1.5">
            {cat.action_kinds.map((k) => { const on = n.policy[lvl].includes(k); return (
              <button type="button" key={k} onClick={() => toggleKind(lvl, k)} className="mono text-[10px] px-2 py-1 rounded-lg border" style={{ borderColor: on ? LEVEL_COLOR[lvl] : "var(--line)", color: on ? LEVEL_COLOR[lvl] : "var(--muted)" }}>{on ? "✓ " : ""}{k}</button>); })}
          </div>
          {!same(n.policy[lvl], defaults.notify.policy[lvl]) && <p className="mono text-[10px] mt-1" style={{ color: "var(--accent)" }}>● CUSTOM (default: {defaults.notify.policy[lvl].join(", ")})</p>}
        </div>))}
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="WhatsApp numbers (comma separated, with country code)" hint={ov?.whatsapp_configured ? `Empty = the ${ov.default_whatsapp_numbers} number(s) from .env.` : "WhatsApp is not configured on the server (WHATSAPP_TOKEN / WHATSAPP_PHONE_ID in .env)."} changed={n.whatsapp_to.length > 0}>
          <input className={inputCls} style={inputStyle} placeholder="919876543210, 919812345678" defaultValue={n.whatsapp_to.join(", ")} key={n.whatsapp_to.join()}
            onBlur={(e) => setN({ whatsapp_to: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></Field>
        <Field label="Webhook URL (Slack, Teams, any JSON endpoint)" hint="Empty = NOTIFY_WEBHOOK_URL from .env" changed={n.webhook_url !== ""}>
          <input className={inputCls} style={inputStyle} placeholder="https://hooks.slack.com/..." value={n.webhook_url} onChange={(e) => setN({ webhook_url: e.target.value.trim() })} /></Field>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <span className="flex items-center gap-2 text-[12px]"><Toggle on={n.news_hazards} onChange={(v) => setN({ news_hazards: v })} label="local news warnings" />Local news hazard warnings</span>
        <span className="flex items-center gap-2 text-[12px]"><Toggle on={n.camera_health} onChange={(v) => setN({ camera_health: v })} label="camera offline notices" />Camera offline / back online notices</span>
      </div>
      <Field label="Extra words that make a local headline a warning" hint="comma separated, e.g. curfew, strike, water cut" changed={n.extra_hazard_words.length > 0}>
        <input className={inputCls} style={inputStyle} defaultValue={n.extra_hazard_words.join(", ")} key={n.extra_hazard_words.join()}
          onBlur={(e) => setN({ extra_hazard_words: e.target.value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean) })} /></Field>
    </div>
  );
}

function Sampling({ cfg, defaults, set }: { cfg: SiteCfg; defaults: SiteCfg; set: (c: SiteCfg) => void }) {
  const s = cfg.sampling, d = defaults.sampling; const up = (p: Partial<SiteCfg["sampling"]>) => set({ ...cfg, sampling: { ...s, ...p } });
  return (
    <div className="space-y-3">
      <p className="text-[12px]" style={{ color: "var(--muted)" }}>Controls how often Drishti looks and how much it spends. A quiet scene is never sent to the AI; these set what counts as “something changed”.</p>
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Look every (seconds)" hint={`default ${d.sample_interval_sec}`} changed={s.sample_interval_sec !== d.sample_interval_sec}><input className={inputCls} style={inputStyle} type="number" min={5} value={s.sample_interval_sec} onChange={(e) => up({ sample_interval_sec: num(e.target.value, d.sample_interval_sec) })} /></Field>
        <Field label="Minimum seconds between AI analyses" hint={`default ${d.min_analysis_gap_sec}`} changed={s.min_analysis_gap_sec !== d.min_analysis_gap_sec}><input className={inputCls} style={inputStyle} type="number" min={0} value={s.min_analysis_gap_sec} onChange={(e) => up({ min_analysis_gap_sec: num(e.target.value, d.min_analysis_gap_sec) })} /></Field>
        <Field label="Change sensitivity (% of picture changed)" hint={`default ${(d.change_fraction * 100).toFixed(1)}% · lower = more sensitive, more AI calls`} changed={s.change_fraction !== d.change_fraction}><input className={inputCls} style={inputStyle} type="number" min={0.5} max={50} step={0.5} value={+(s.change_fraction * 100).toFixed(1)} onChange={(e) => up({ change_fraction: num(e.target.value, d.change_fraction * 100) / 100 })} /></Field>
        <Field label="Frames kept for a motion burst (1–8)" hint={`default ${d.burst_frames}`} changed={s.burst_frames !== d.burst_frames}><input className={inputCls} style={inputStyle} type="number" min={1} max={8} value={s.burst_frames} onChange={(e) => up({ burst_frames: num(e.target.value, d.burst_frames) })} /></Field>
      </div>
      <span className="flex items-center gap-2 text-[12px]"><Toggle on={s.storyboard} onChange={(v) => up({ storyboard: v })} label="storyboard" />Show the AI a before / during / now contact sheet (understands movement and fights better)</span>
    </div>
  );
}

/* ------------------------------------------------------------------ site details */
function SiteDetails({ t, sop, reload }: { t: AdminTenant; sop: string; reload: () => void }) {
  const [name, setName] = useState(t.name), [loc, setLoc] = useState(t.location), [text, setText] = useState(sop);
  const [err, setErr] = useState<string | null>(null), [ok, setOk] = useState<string | null>(null);
  useEffect(() => { setName(t.name); setLoc(t.location); setText(sop); setErr(null); setOk(null); }, [t.id, t.name, t.location, sop]);
  const go = async (fn: () => Promise<string>) => { setErr(null); setOk(null); try { setOk(await fn()); reload(); } catch (x) { setErr(msg(x)); } };
  return (
    <div className="space-y-4">
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Site name"><input className={inputCls} style={inputStyle} maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="City / location" hint="Used to find local news for this site"><input className={inputCls} style={inputStyle} maxLength={120} value={loc} onChange={(e) => setLoc(e.target.value)} /></Field>
      </div>
      <button className="mono text-[11px] px-3 py-2 rounded-lg" style={btnPrimary} onClick={() => go(async () => { await api.saveAdminSite(t.id, { name: name.trim(), location: loc.trim() }); return "Site details saved."; })}>SAVE SITE DETAILS</button>
      <Field label={`Safety procedure (SOP) - shared by every ${t.vertical_label} site`} hint="The AI cites this when explaining an alert.">
        <textarea className={inputCls + " h-40 mono text-[12px]"} style={inputStyle} value={text} onChange={(e) => setText(e.target.value)} /></Field>
      <button className="mono text-[11px] px-3 py-2 rounded-lg border" style={btnGhost} onClick={() => go(async () => (await api.saveAdminSop(t.id, text)).note)}>SAVE SOP</button>
      <ErrorLine msg={err} />{ok && <p className="mono text-[11px]" style={{ color: "var(--ok)" }}>✓ {ok}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ editor */
type TabId = "connection" | "alarms" | "custom" | "notify" | "sampling" | "site";
function Editor({ t, camera, ov, reload }: { t: AdminTenant; camera: string; ov: AdminOverview | undefined; reload: () => void }) {
  const [tab, setTab] = useState<TabId>(camera ? "connection" : "alarms");
  const [data, setData] = useState<AdminConfig | null>(null);
  const [draft, setDraft] = useState<SiteCfg | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.adminConfig(t.id, camera).then((c) => { setData(c); setDraft(clone(c.effective)); }).catch((x) => setErr(msg(x)));
  useEffect(() => { setData(null); setDraft(null); setErr(null); setOk(null); setTab(camera ? "connection" : "alarms"); void load(); /* eslint-disable-next-line */ }, [t.id, camera]);

  const dirty = useMemo(() => !!data && !!draft && !same(draft, data.effective), [data, draft]);
  const customised = useMemo(() => !!data && !same(data.effective, data.defaults), [data]);
  const save = async () => { if (!draft) return; setBusy(true); setErr(null); setOk(null);
    try { const c = await api.saveAdminConfig(t.id, camera, draft); setData(c); setDraft(clone(c.effective)); setOk("Saved. Every agent picks this up within seconds, no restart needed."); reload(); }
    catch (x) { setErr(msg(x)); } finally { setBusy(false); } };
  const reset = async () => { if (!window.confirm(camera ? "Remove this camera's own settings and inherit the site's?" : "Reset this site to the built-in defaults?")) return;
    setBusy(true); setErr(null); setOk(null);
    try { await api.resetAdminConfig(t.id, camera); await load(); setOk("Reset."); reload(); } catch (x) { setErr(msg(x)); } finally { setBusy(false); } };

  const tabs: [TabId, string][] = [...(camera ? [["connection", "Connection"] as [TabId, string]] : []), ["alarms", "Alarms"], ["custom", "Custom rules"], ["notify", "Notifications"], ["sampling", "Sampling"], ...(camera ? [] : [["site", "Site & SOP"] as [TabId, string]])];
  const editing = tab !== "connection" && tab !== "site";
  return (
    <Panel kicker={camera ? "CAMERA SETTINGS" : "SITE SETTINGS"} title={camera ? `${t.name} · ${camera}` : t.name}
      right={customised ? <Pill color="var(--accent)">CUSTOMISED</Pill> : <Pill color="var(--muted)">DEFAULTS</Pill>}>
      {camera && <p className="text-[11px] mb-3" style={{ color: "var(--muted)" }}>Settings you change here apply to this camera only; everything else is inherited from the site.</p>}
      <div className="flex gap-1 flex-wrap mb-4 mono text-[11px] tracking-wide">
        {tabs.map(([id, label]) => <button key={id} onClick={() => setTab(id)} className="px-3 py-1.5 rounded-lg border" style={{ borderColor: tab === id ? "var(--accent)" : "var(--line)", color: tab === id ? "var(--accent)" : "var(--muted)" }}>{label}</button>)}
      </div>
      {tab === "connection" && <Connection tenant={t.id} camera={camera} reload={reload} />}
      {tab === "site" && data && <SiteDetails t={t} sop={data.sop} reload={reload} />}
      {editing && !draft && <Empty>{err ?? "Loading..."}</Empty>}
      {editing && data && draft && (
        <>
          {tab === "alarms" && <Alarms cfg={draft} defaults={data.defaults} cat={data.catalog} set={setDraft} />}
          {tab === "custom" && <Custom cfg={draft} set={setDraft} />}
          {tab === "notify" && <Notify cfg={draft} defaults={data.defaults} cat={data.catalog} ov={ov} set={setDraft} />}
          {tab === "sampling" && <Sampling cfg={draft} defaults={data.defaults} set={setDraft} />}
          <div className="sticky bottom-0 -mx-4 -mb-4 mt-5 px-4 py-3 border-t flex flex-wrap items-center gap-2 rounded-b-xl" style={{ background: "var(--surface)", borderColor: "var(--line)" }}>
            <button disabled={!dirty || busy} onClick={save} className="mono text-[11px] px-3 py-2 rounded-lg flex items-center gap-1.5 disabled:opacity-40" style={btnPrimary}><Save size={12} />SAVE CHANGES</button>
            <button disabled={!dirty || busy} onClick={() => data && setDraft(clone(data.effective))} className="mono text-[11px] px-3 py-2 rounded-lg border disabled:opacity-40" style={btnGhost}>DISCARD</button>
            <button disabled={!customised || busy} onClick={reset} className="mono text-[11px] px-3 py-2 rounded-lg border flex items-center gap-1.5 disabled:opacity-40" style={btnGhost}><RotateCcw size={12} />{camera ? "INHERIT FROM SITE" : "RESET TO DEFAULTS"}</button>
            {dirty && <span className="mono text-[10px]" style={{ color: "var(--warn)" }}>unsaved changes</span>}
          </div>
        </>)}
      <div className="mt-2"><ErrorLine msg={err} />{ok && <p className="mono text-[11px]" style={{ color: "var(--ok)" }}><Check size={11} className="inline" /> {ok}</p>}</div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ page */
export default function Admin() {
  const ov = usePoll(api.adminOverview, 10_000);
  const [tid, setTid] = useState<string>("");
  const [cam, setCam] = useState<string>("");
  const [newCam, setNewCam] = useState("");
  const tenants = ov.data?.tenants ?? [];
  useEffect(() => { if (!tid && tenants.length) setTid(tenants[0].id); }, [tid, tenants]);
  const t = tenants.find((x) => x.id === tid);

  return (
    <div className="pt-5 space-y-4">
      <div>
        <h2 className="text-xl font-bold">Admin</h2>
        <p className="text-[13px]" style={{ color: "var(--muted)" }}>Choose a site or one of its cameras, then set what to watch for, which alarms to raise, who is told, and how to reach the camera. Changes are stored in the database and applied within seconds. No .env edits, no restart.</p>
      </div>
      {ov.data && !ov.data.auth_required && (
        <div className="rounded-lg border px-3 py-2 text-[12px] flex gap-2 items-start" style={{ borderColor: "var(--warn)", color: "var(--warn)" }}>
          <ShieldAlert size={14} className="mt-0.5 shrink-0" /><span>Anyone who can reach this dashboard can change these settings. Before exposing it beyond this laptop set <span className="mono">AUTH_REQUIRED=true</span> and <span className="mono">ADMIN_TOKEN</span> in .env.</span>
        </div>)}
      {ov.data && !ov.data.secrets_key_custom && (
        <p className="text-[11px]" style={{ color: "var(--muted)" }}>Camera passwords are encrypted with a key derived from POSTGRES_PASSWORD. For anything beyond a laptop set <span className="mono">SECRETS_KEY</span> in .env.</p>)}
      <ErrorLine msg={ov.error} />
      <div className="grid lg:grid-cols-[18rem_1fr] gap-4 items-start">
        <Panel kicker="SITES & CAMERAS">
          {!tenants.length && <Empty>{ov.loading ? "Loading..." : "No sites yet."}</Empty>}
          <ul className="space-y-3">
            {tenants.map((x) => (
              <li key={x.id}>
                <button onClick={() => { setTid(x.id); setCam(""); }} className="w-full text-left rounded-lg border px-2.5 py-2"
                  style={{ borderColor: tid === x.id && !cam ? "var(--accent)" : "var(--line)", background: tid === x.id && !cam ? "var(--surface2)" : "transparent" }}>
                  <p className="text-sm font-semibold truncate">{x.name}</p>
                  <p className="mono text-[10px]" style={{ color: "var(--muted)" }}>{x.vertical_label}{x.location ? ` · ${x.location}` : ""}{x.customised ? " · ●" : ""}</p>
                </button>
                <ul className="mt-1 ml-3 space-y-1">
                  {x.cameras.map((c) => (
                    <li key={c.camera_id}>
                      <button onClick={() => { setTid(x.id); setCam(c.camera_id); }} className="w-full text-left rounded-lg border px-2 py-1.5 flex items-center gap-2"
                        style={{ borderColor: tid === x.id && cam === c.camera_id ? "var(--accent)" : "var(--line)" }}>
                        <Camera size={12} style={{ color: !c.enabled ? "var(--muted)" : c.ok === false ? "var(--bad)" : "var(--ok)" }} />
                        <span className="mono text-[11px] truncate flex-1">{c.camera_id}{c.customised ? " ●" : ""}</span>
                        <Chip>{c.enabled ? c.source : "paused"}</Chip>
                      </button>
                    </li>))}
                  {tid === x.id && (
                    <li><form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); const id = newCam.trim(); if (id) { setCam(id); setNewCam(""); } }}>
                      <input className={inputCls + " !py-1 !text-[12px]"} style={inputStyle} placeholder="new camera id" pattern="[A-Za-z0-9_\-]+" maxLength={40} value={newCam} onChange={(e) => setNewCam(e.target.value)} />
                      <button className="px-2 rounded-lg border" style={btnGhost} aria-label="Add camera"><Plus size={12} /></button></form></li>)}
                </ul>
              </li>))}
          </ul>
          <p className="mono text-[10px] mt-3" style={{ color: "var(--muted)" }}>● = has custom settings</p>
        </Panel>
        {t ? (<div className="space-y-4 min-w-0"><ConnectCamera key={`c-${t.id}`} tenant={t.id} camera={cam} /><Editor key={`${t.id}/${cam}`} t={t} camera={cam} ov={ov.data} reload={ov.reload} /></div>) : <Empty>Select a site.</Empty>}
      </div>
    </div>
  );
}
