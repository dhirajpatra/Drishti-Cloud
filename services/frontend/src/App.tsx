import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Moon, Sun } from "lucide-react";
import { api, getToken, onUnauthorized, setToken } from "./api";
import { usePoll } from "./lib";
import Dashboard from "./pages/Dashboard";
import Architecture from "./pages/Architecture";
import Admin from "./pages/Admin";
import { Pill, btnPrimary, inputStyle } from "./components/ui";
import { TenantAdmin } from "./components/TenantAdmin";

type Tab = "dashboard" | "admin" | "architecture";
const tabFromHash = (): Tab => (window.location.hash === "#architecture" ? "architecture" : window.location.hash === "#admin" ? "admin" : "dashboard");

function TokenPrompt({ onDone }: { onDone: () => void }) {
  const [val, setVal] = useState(getToken());
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setToken(val.trim());
    onDone();
  };
  return (
    <form onSubmit={submit} className="flex items-center gap-2">
      <KeyRound size={14} style={{ color: "var(--warn)" }} />
      <input type="password" value={val} onChange={(e) => setVal(e.target.value)} placeholder="Admin token required"
        className="rounded-lg px-2 py-1 text-[12px] border outline-none w-40 sm:w-52" style={{ ...inputStyle, borderColor: "var(--warn)" }} />
      <button className="mono text-[11px] px-3 py-1 rounded-lg" style={btnPrimary}>SAVE</button>
    </form>
  );
}

function HealthPill() {
  const h = usePoll(api.health, 10000);
  const d = h.data;
  if (!d) return <Pill color={h.error ? "var(--bad)" : "var(--muted)"}>{h.error ? "OFFLINE" : "..."}</Pill>;
  const ok = d.status === "ok";
  const bad = (["redis", "postgres", "neo4j"] as const).filter((k) => !d[k]);
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <span title={bad.length ? `down: ${bad.join(", ")}` : "all services up"}>
        <Pill color={ok ? "var(--ok)" : "var(--warn)"}>{ok ? "OK" : "DEGRADED"}</Pill>
      </span>
      {d.mock_mode && <Pill color="var(--accent2)">MOCK MODE</Pill>}
      {d.low_cost_mode && <Pill color="var(--accent)">LOW COST</Pill>}
    </div>
  );
}

type Theme = "light" | "dark";
const initialTheme = (): Theme => {
  try { return localStorage.getItem("drishti_theme") === "dark" ? "dark" : "light"; } catch { return "light"; }
};

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0B0F17" : "#F4F6FA");
    try { localStorage.setItem("drishti_theme", theme); } catch { /* private window: keep it for this visit only */ }
  }, [theme]);
  const [tab, setTab] = useState<Tab>(tabFromHash);
  const [needToken, setNeedToken] = useState(false);
  const [selectedTenantId, setSelectedTenantId] = useState<string>("home_1_bangalore");

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    const off = onUnauthorized(() => setNeedToken(true));
    return () => {
      window.removeEventListener("hashchange", onHash);
      off();
    };
  }, []);

  const go = (t: Tab) => {
    window.location.hash = t;
    setTab(t);
  };

  return (
    <div className="min-h-screen overflow-x-clip" style={{ background: "var(--bg)" }}>
      <header className="sticky top-0 z-40 backdrop-blur border-b" style={{ background: "var(--header)", borderColor: "var(--line)" }}>
        <div className="max-w-7xl mx-auto px-4 py-3 flex flex-wrap items-center gap-x-5 gap-y-2">
          <div className="flex items-center gap-3">
            <img src="/logo.png" alt="Drishti" width={40} height={40} className="w-10 h-10 object-contain drishti-logo" />
            <div>
              <p className="font-bold leading-none">DRISHTI CLOUD</p>
              <p className="mono text-[10px] tracking-widest hidden sm:block" style={{ color: "var(--muted)" }}>
                SECURITY SENTINEL CONSOLE
              </p>
            </div>
          </div>
          <nav className="flex gap-1 mono text-[11px] tracking-widest">
            {(["dashboard", "admin", "architecture"] as Tab[]).map((t) => (
              <button key={t} onClick={() => go(t)} className="px-3 py-1.5 rounded-lg border uppercase"
                style={{ borderColor: tab === t ? "var(--accent)" : "var(--line)", color: tab === t ? "var(--accent)" : "var(--muted)", background: tab === t ? "var(--surface2)" : "transparent" }}>{t}</button>
            ))}
          </nav>
          <div className="flex items-center gap-3 flex-wrap ml-auto">
            {needToken && <TokenPrompt onDone={() => setNeedToken(false)} />}
            <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Light theme" : "Dark theme"} className="p-2 rounded-lg border" style={{ borderColor: "var(--line)", color: "var(--muted)" }}>
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
            </button>
            <HealthPill />
          </div>
        </div>
      </header>
      {tab === "dashboard" ? <main className="max-w-7xl mx-auto px-4"><Dashboard /></main> : tab === "admin" ? <main className="max-w-7xl mx-auto px-4 pb-10"><Admin /></main> : <Architecture />}
    </div>
  );
}