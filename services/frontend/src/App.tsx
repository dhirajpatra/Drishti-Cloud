import { useEffect, useState, type FormEvent } from "react";
import { Eye, KeyRound } from "lucide-react";
import { api, getToken, onUnauthorized, setToken } from "./api";
import { usePoll } from "./lib";
import Dashboard from "./pages/Dashboard";
import Architecture from "./pages/Architecture";
import { Pill, btnPrimary, inputStyle } from "./components/ui";

type Tab = "dashboard" | "architecture";
const tabFromHash = (): Tab => (window.location.hash === "#architecture" ? "architecture" : "dashboard");

function TokenPrompt({ onDone }: { onDone: () => void }) {
  const [val, setVal] = useState(getToken());
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setToken(val.trim());
    onDone();
  };
  return (
    <form onSubmit={submit} className="flex items-center gap-2">
      <KeyRound size={14} style={{ color: "#fbbf24" }} />
      <input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="Admin token required"
        className="rounded-lg px-2 py-1 text-[12px] border outline-none w-40 sm:w-52" style={{ ...inputStyle, borderColor: "#fbbf24" }} />
      <button className="mono text-[11px] px-3 py-1 rounded-lg" style={btnPrimary}>SAVE</button>
    </form>
  );
}

function HealthPill() {
  const h = usePoll(api.health, 10000);
  const d = h.data;
  if (!d) return <Pill color={h.error ? "#f87171" : "#8ea3c2"}>{h.error ? "OFFLINE" : "..."}</Pill>;
  const ok = d.status === "ok";
  const bad = (["redis", "postgres", "neo4j"] as const).filter((k) => !d[k]);
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <span title={bad.length ? `down: ${bad.join(", ")}` : "all services up"}>
        <Pill color={ok ? "#4ade80" : "#fbbf24"}>{ok ? "OK" : "DEGRADED"}</Pill>
      </span>
      {d.mock_mode && <Pill color="#a78bfa">MOCK MODE</Pill>}
      {d.low_cost_mode && <Pill color="#22d3ee">LOW COST</Pill>}
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [needToken, setNeedToken] = useState(false);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    const off = onUnauthorized(() => setNeedToken(true));
    return () => { window.removeEventListener("hashchange", onHash); off(); };
  }, []);

  const go = (t: Tab) => { window.location.hash = t; setTab(t); };

  return (
    <div className="min-h-screen overflow-x-clip" style={{ background: "var(--bg)" }}>
      <header className="sticky top-0 z-40 backdrop-blur border-b" style={{ background: "rgba(7,11,18,0.92)", borderColor: "var(--line)" }}>
        <div className="max-w-7xl mx-auto px-4 py-3 flex flex-wrap items-center gap-x-5 gap-y-2">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg grid place-items-center font-bold" style={{ background: "var(--accent)", color: "#04121a" }}><Eye size={20} /></div>
            <div>
              <p className="font-bold leading-none">DRISHTI CLOUD</p>
              <p className="mono text-[10px] tracking-widest hidden sm:block" style={{ color: "var(--muted)" }}>SECURITY SENTINEL CONSOLE</p>
            </div>
          </div>
          <nav className="flex gap-1 mono text-[11px] tracking-widest">
            {(["dashboard", "architecture"] as Tab[]).map((t) => (
              <button key={t} onClick={() => go(t)} className="px-3 py-1.5 rounded-lg border uppercase"
                style={{ borderColor: tab === t ? "var(--accent)" : "var(--line)", color: tab === t ? "var(--accent)" : "var(--muted)", background: tab === t ? "var(--surface2)" : "transparent" }}>{t}</button>
            ))}
          </nav>
          <div className="flex items-center gap-3 flex-wrap ml-auto">
            {needToken && <TokenPrompt onDone={() => setNeedToken(false)} />}
            <HealthPill />
          </div>
        </div>
      </header>
      {tab === "dashboard" ? <main className="max-w-7xl mx-auto px-4"><Dashboard /></main> : <Architecture />}
    </div>
  );
}
