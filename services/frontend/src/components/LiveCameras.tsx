import { useEffect, useRef, useState } from "react";
import { Camera, ImageOff } from "lucide-react";
import { api, type CameraInfo } from "../api";
import { relTime, useNow, usePoll } from "../lib";
import { Empty, Panel } from "./ui";

/** Latest frame of one camera. Re-fetched every few seconds; the previous object URL is released each time. */
function CameraTile({ tenantId, cam, now }: { tenantId: string; cam: CameraInfo; now: number }) {
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  const last = useRef<string>();

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const u = URL.createObjectURL(await api.cameraFrameBlob(tenantId, cam.camera_id));
        if (!alive) { URL.revokeObjectURL(u); return; }
        if (last.current) URL.revokeObjectURL(last.current);
        last.current = u;
        setUrl(u);
        setFailed(false);
      } catch { if (alive) setFailed(true); }
    };
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); if (last.current) URL.revokeObjectURL(last.current); last.current = undefined; };
  }, [tenantId, cam.camera_id]);

  const age = cam.updated_at ? now - new Date(cam.updated_at).getTime() : Infinity;
  const stale = age > 5 * 60_000;
  return (
    <figure className="rounded-lg border overflow-hidden" style={{ borderColor: "var(--line)", background: "#05080f" }}>
      <div className="aspect-[4/3] grid place-items-center">
        {url && !failed ? <img src={url} alt={`Camera ${cam.camera_id}`} className="w-full h-full object-cover" />
          : <ImageOff size={22} style={{ color: "var(--muted)" }} />}
      </div>
      <figcaption className="flex items-center justify-between px-2 py-1.5 mono text-[11px]">
        <span className="flex items-center gap-1.5"><Camera size={12} />{cam.camera_id}</span>
        <span style={{ color: stale ? "#f59e0b" : "var(--muted)" }}>{cam.updated_at ? relTime(cam.updated_at, now) : "-"}{stale ? " · stale" : ""}</span>
      </figcaption>
    </figure>
  );
}

export function LiveCameras({ tenantId }: { tenantId: string }) {
  const cams = usePoll(() => api.cameras(tenantId), 5000, [tenantId]);
  const now = useNow();
  return (
    <Panel kicker="LIVE CAMERAS" title="Latest frame per camera"
      right={<span className="mono text-[11px]" style={{ color: "var(--muted)" }}>updates when a frame is sampled</span>}>
      {cams.data && cams.data.length > 0 ? (
        <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {cams.data.map((c) => <CameraTile key={c.camera_id} tenantId={tenantId} cam={c} now={now} />)}
        </div>
      ) : (
        <Empty>{cams.loading && !cams.data ? "Loading..."
          : "No frames received yet. Add the camera to RTSP_FEEDS in .env (tenant:vertical:camera=url) and restart feed-ingestor."}</Empty>
      )}
    </Panel>
  );
}
