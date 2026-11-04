import { useState } from "react";
import { Copy, KeyRound, Trash2 } from "lucide-react";
import { api, type EdgeToken } from "../api";
import { relTime, useNow, usePoll } from "../lib";
import { Chip, Empty, ErrorLine, Panel, btnGhost, btnPrimary, inputCls, inputStyle } from "./ui";

type Mode = "lan" | "edge" | "vpn" | "tunnel";
const MODES: { id: Mode; title: string; sub: string }[] = [
  { id: "edge", title: "Edge agent", sub: "Recommended. Nothing to open: a small agent at the site pushes pictures to Drishti." },
  { id: "lan", title: "Same network", sub: "Drishti runs on the same Wi-Fi / LAN as the camera. Use the form below." },
  { id: "vpn", title: "VPN", sub: "Tailscale or WireGuard joins the site and Drishti into one private network." },
  { id: "tunnel", title: "Tunnel", sub: "ngrok TCP tunnel: quick for a demo, the address changes on restart." },
];

const Code = ({ text }: { text: string }) => (
  <div className="relative">
    <pre className="mono text-[11px] rounded-lg border p-2.5 overflow-x-auto whitespace-pre-wrap break-all" style={{ borderColor: "var(--line)", background: "var(--field)" }}>{text}</pre>
    <button type="button" aria-label="Copy" onClick={() => void navigator.clipboard?.writeText(text)} className="absolute top-1.5 right-1.5 p-1 rounded border" style={btnGhost}><Copy size={11} /></button>
  </div>
);

function Edge({ tenant, camera }: { tenant: string; camera: string }) {
  const tokens = usePoll(() => api.edgeTokens(tenant), 15_000, [tenant]);
  const now = useNow(15_000);
  const [label, setLabel] = useState("");
  const [fresh, setFresh] = useState<EdgeToken | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const gateway = typeof window !== "undefined" ? window.location.origin.replace(/:3000$/, ":8000") : "https://YOUR-DRISHTI";
  const pair = async () => {
    setBusy(true); setErr(null);
    try { setFresh(await api.createEdgeToken(tenant, label.trim() || "site")); setLabel(""); tokens.reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const revoke = async (id: string) => { try { await api.revokeEdgeToken(tenant, id); tokens.reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } };
  const cam = camera || "cam_1";
  return (
    <div className="space-y-3">
      <p className="text-[12px]" style={{ color: "var(--muted)" }}>
        Run a tiny agent on any computer at the camera's site. It reads the private RTSP address locally and sends pictures out over HTTPS, so there is no VPN, ngrok or open port, and the camera password never leaves the site.
      </p>
      <div className="flex flex-wrap gap-2">
        <input className={inputCls + " !w-56"} style={inputStyle} placeholder="site label, e.g. ICU ward" maxLength={60} value={label} onChange={(e) => setLabel(e.target.value)} />
        <button type="button" disabled={busy} onClick={() => void pair()} className="mono text-[11px] px-3 py-2 rounded-lg border flex items-center gap-1.5 disabled:opacity-50" style={btnPrimary}><KeyRound size={12} />PAIR A SITE</button>
      </div>
      <ErrorLine msg={err ?? tokens.error} />
      {fresh?.token && (
        <div className="space-y-1.5">
          <p className="text-[12px]" style={{ color: "var(--warn)" }}>Copy this now: the token is shown once. Put it in the .env file at the site (<span className="mono">edge/camera-agent/.env</span>), then run <span className="mono">docker compose up -d --build</span> there.</p>
          <Code text={`GATEWAY_URL=${gateway}\nTENANT_ID=${tenant}\nEDGE_TOKEN=${fresh.token}\nCAMERAS=${cam}=rtsp://USER:PASSWORD@192.168.1.50:554/stream1`} />
        </div>)}
      {tokens.data && tokens.data.length === 0 && !fresh && <Empty>No site is paired yet.</Empty>}
      <ul className="space-y-1">
        {tokens.data?.map((t) => (
          <li key={t.id} className="flex items-center gap-2 text-sm">
            <Chip color={t.revoked ? "var(--bad)" : t.last_seen ? "var(--ok)" : "var(--warn)"}>{t.revoked ? "revoked" : t.last_seen ? "connected" : "waiting"}</Chip>
            <span className="font-semibold">{t.label || "site"}</span>
            <span className="mono text-[10px]" style={{ color: "var(--muted)" }}>{t.last_seen ? `last picture ${relTime(t.last_seen, now)}` : "no picture yet"}</span>
            {!t.revoked && <button type="button" aria-label="Revoke" onClick={() => void revoke(t.id)} className="ml-auto p-1 rounded border" style={btnGhost}><Trash2 size={12} /></button>}
          </li>))}
      </ul>
    </div>
  );
}

const VPN = `# at the camera site (Docker)
export TS_AUTHKEY=tskey-...   TS_ROUTES=192.168.1.0/24
docker compose -f deploy/remote/tailscale-site.compose.yml up -d
# approve the subnet route in the Tailscale admin console, install Tailscale on the Drishti machine,
# then keep the camera URL as it is: rtsp://192.168.1.50:554/...`;
const TUNNEL = `# at the camera site (Docker)
export NGROK_AUTHTOKEN=...  CAMERA_HOST=192.168.1.50  CAMERA_PORT=554
docker compose -f deploy/remote/ngrok-site.compose.yml up
# ngrok prints tcp://0.tcp.in.ngrok.io:12345  ->  protocol rtsp, host 0.tcp.in.ngrok.io, port 12345
# The address changes on restart: update host and port below, then TEST CONNECTION.`;

/** Pick HOW Drishti reaches a private camera. Every route ends in the same place: pictures arrive, Drishti works as usual. */
export function ConnectCamera({ tenant, camera }: { tenant: string; camera: string }) {
  const [mode, setMode] = useState<Mode>("edge");
  return (
    <Panel kicker="CONNECT A CAMERA" title="Reach a private RTSP camera from anywhere">
      <div className="grid sm:grid-cols-2 gap-2 mb-3">
        {MODES.map((m) => (
          <button key={m.id} type="button" onClick={() => setMode(m.id)} className="text-left rounded-lg border px-3 py-2"
            style={{ borderColor: mode === m.id ? "var(--accent)" : "var(--line)", background: mode === m.id ? "var(--surface2)" : "transparent" }}>
            <p className="text-sm font-semibold">{m.title}</p>
            <p className="text-[11px]" style={{ color: "var(--muted)" }}>{m.sub}</p>
          </button>))}
      </div>
      {mode === "edge" && <Edge tenant={tenant} camera={camera} />}
      {mode === "lan" && <p className="text-[12px]" style={{ color: "var(--muted)" }}>Fill in the camera form below (protocol, host, port, path, username, password) and press TEST CONNECTION. It checks DNS, the port, the RTSP answer and a real picture, and tells you which step failed.</p>}
      {mode === "vpn" && <div className="space-y-2"><p className="text-[12px]" style={{ color: "var(--muted)" }}>Two sites that both use 192.168.1.x clash on a VPN: use the edge agent in that case. WireGuard and OpenVPN steps are in docs/REMOTE_CAMERAS.md.</p><Code text={VPN} /></div>}
      {mode === "tunnel" && <div className="space-y-2"><p className="text-[12px]" style={{ color: "var(--muted)" }}>Free tunnels can need a verified account, change address on restart, and the provider carries the stream. Fine for a demo, not for a hospital.</p><Code text={TUNNEL} /></div>}
    </Panel>
  );
}
