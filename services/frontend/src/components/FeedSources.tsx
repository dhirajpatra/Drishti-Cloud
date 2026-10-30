import { useState, type FormEvent } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import { usePoll } from "../lib";
import { Empty, ErrorLine, Panel, btnGhost, btnPrimary, inputCls, inputStyle } from "./ui";

/** Connect another camera (room, gate, ward) to this tenant without touching .env. The feed-ingestor picks it up on its next sample. */
export function FeedSources({ tenantId }: { tenantId: string }) {
  const feeds = usePoll(() => api.feeds(tenantId), 15_000, [tenantId]);
  const [cam, setCam] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await api.addFeed(tenantId, cam.trim(), url.trim()); setCam(""); setUrl(""); feeds.reload(); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    finally { setBusy(false); }
  };
  const remove = async (c: string) => {
    setBusy(true); setErr(null);
    try { await api.removeFeed(tenantId, c); feeds.reload(); }
    catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
    finally { setBusy(false); }
  };

  return (
    <Panel kicker="CAMERA SOURCES" title="Connect a room, gate or ward">
      <form onSubmit={add} className="grid sm:grid-cols-[10rem_1fr_auto] gap-2">
        <input className={inputCls} style={inputStyle} placeholder="camera id (e.g. bedroom)" value={cam} onChange={(e) => setCam(e.target.value)} required maxLength={40} pattern="[A-Za-z0-9_\-]+" />
        <input className={inputCls} style={inputStyle} placeholder="http://192.168.1.4:8081/  ·  rtsp://user:pw@10.0.0.5/stream1  ·  https://.../live.m3u8" value={url} onChange={(e) => setUrl(e.target.value)} required />
        <button disabled={busy} className="mono text-[11px] px-3 py-2 rounded-lg flex items-center justify-center gap-1.5 disabled:opacity-50" style={btnPrimary}><Plus size={12} />ADD</button>
      </form>
      <p className="text-[11px] mt-2" style={{ color: "var(--muted)" }}>
        Works with motionEye / ESP32-CAM / phone-app snapshot or MJPEG URLs, RTSP cameras and HLS (.m3u8) live URLs. Facebook / Instagram Live pages cannot be read (Meta has no API for it).
      </p>
      <ErrorLine msg={err ?? feeds.error} />
      {feeds.data && feeds.data.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {feeds.data.map((f) => (
            <li key={f.camera_id} className="flex items-center gap-2 rounded-lg border px-2.5 py-1.5" style={{ borderColor: "var(--line)" }}>
              <span className="mono text-[11px] shrink-0">{f.camera_id}</span>
              <span className="mono text-[11px] truncate min-w-0 flex-1" style={{ color: "var(--muted)" }}>{f.url}</span>
              <button disabled={busy} onClick={() => remove(f.camera_id)} aria-label={`Remove ${f.camera_id}`} className="p-1.5 rounded-lg border disabled:opacity-50" style={btnGhost}><Trash2 size={12} /></button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-3"><Empty>{feeds.loading && !feeds.data ? "Loading..." : "No cameras added here yet. Cameras from RTSP_FEEDS in .env are not listed."}</Empty></div>
      )}
    </Panel>
  );
}
