// Typed fetch helpers for the Drishti gateway.

export const API_BASE: string = (import.meta.env.VITE_API_URL as string | undefined) || "/api";
export const TOKEN_KEY = "drishti_admin_token";
export const TOKEN_EVENT = "drishti-token-changed";

export type VerticalId = "mall_security" | "home_seniors" | "school_children" | "icu_hospital";
export type AlertLevel = "ALERT" | "CRITICAL";
export type EventLevel = "NORMAL" | "ALERT" | "CRITICAL";

export interface Health {
  status: "ok" | "degraded";
  redis: boolean;
  postgres: boolean;
  neo4j: boolean;
  mock_mode: boolean;
  low_cost_mode: boolean;
}
export interface Vertical { id: VerticalId; label: string; tenant_type: string; sam_labels: string[] }
export interface Tenant {
  id: string; vertical: VerticalId; name: string; location: string; created_at: string;
  alerts_24h: number; events_24h: number; last_seen: string | null;
}
export interface NewTenantBody { id: string; vertical: VerticalId; name?: string; location?: string }
export interface CreatedTenant {
  id: string; vertical: VerticalId; name: string; location: string; api_key: string; created_at: string;
}
export interface Alert {
  id: string; tenant_id: string; vertical: VerticalId; level: AlertLevel; rule: string; reason: string;
  rationale: string; speak_text: string; objects: string[]; actions: string[]; frame_id: string;
  created_at: string; acknowledged: boolean; has_image: boolean;
}
export interface EventRow {
  frame_id: string; camera_id: string; level: EventLevel; decision: string | null; reason: string;
  rationale: string; objects: string[]; created_at: string;
}
export interface Actuation {
  id: string; alert_id: string; kind: string; payload: Record<string, unknown>; status: string; created_at: string;
}
export interface GraphNode { id: string; labels: string[]; props: Record<string, unknown> }
export interface GraphEdge { source: string; target: string; type: string }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[] }
export interface VerticalStats {
  vertical: string; tenants: number; events_24h: number; alerts_24h: number; critical_24h: number;
  by_level: { NORMAL: number; ALERT: number; CRITICAL: number };
}
export interface IngestResult { status: "queued"; frame_id: string; static: boolean; change?: number | null }
export interface Notification {
  id: string; ts: string; kind: "news" | "alert" | string; level: "CRITICAL" | "WARNING" | "INFO";
  title: string; detail: string; url: string | null; read: boolean;
}
export interface NotificationList { unread: number; items: Notification[] }
export interface Usage { date: string; models: Record<string, Record<string, number>> }

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable */ }
  window.dispatchEvent(new Event(TOKEN_EVENT));
}

// 401 handling: listeners are told a token is required; the UI saves a token then calls retry.
type UnauthorizedListener = () => void;
const listeners = new Set<UnauthorizedListener>();
export function onUnauthorized(fn: UnauthorizedListener): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const t = getToken();
  if (t) h["X-Admin-Token"] = t;
  return h;
}

async function raw(path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers: headers(init.headers as Record<string, string> | undefined) });
  } catch {
    throw new ApiError(0, "Gateway unreachable");
  }
  if (res.status === 401) listeners.forEach((fn) => fn());
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
      else if (j.detail) detail = JSON.stringify(j.detail);
    } catch { /* non-JSON body */ }
    throw new ApiError(res.status, detail);
  }
  return res;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await raw(path, init);
  return (await res.json()) as T;
}

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface FeedInfo { camera_id: string; url: string }
export interface CameraInfo { camera_id: string; updated_at: string | null }

export interface RuleCfg { enabled: boolean; level: AlertLevel | null; cooldown_sec: number | null; hours: [number, number] | null }
export interface CustomRule { id: string; text: string; level: AlertLevel; enabled: boolean; hours: [number, number] | null }
export interface LabelRule { id: string; label: string; op: "present" | "absent" | "count_gte"; value: number; for_sec: number; level: AlertLevel; message: string; enabled: boolean; hours: [number, number] | null }
export interface SiteCfg {
  rules: Record<string, RuleCfg>;
  thresholds: Record<string, number>;
  sampling: { sample_interval_sec: number; change_fraction: number; min_analysis_gap_sec: number; burst_frames: number; storyboard: boolean };
  notify: { policy: Record<AlertLevel, string[]>; whatsapp_to: string[]; webhook_url: string; news_hazards: boolean; camera_health: boolean; extra_hazard_words: string[] };
  custom_rules: CustomRule[]; label_rules: LabelRule[]; instructions: string;
}
export interface Catalog {
  vertical: VerticalId; vertical_label: string; levels: AlertLevel[]; action_kinds: string[]; schemes: string[];
  rules: { id: string; label: string; description: string; default_level: AlertLevel }[];
  thresholds: { key: string; label: string; unit: string; min: number; max: number }[];
}
export interface AdminConfig { scope: string; camera: string; defaults: SiteCfg; overrides: Partial<SiteCfg>; effective: SiteCfg; catalog: Catalog; sop: string }
export interface AdminCamera { camera_id: string; source: "database" | "env" | "edge"; enabled: boolean; last_frame: string | null; level: string | null; ok: boolean | null; error: string; customised: boolean }
export interface AdminTenant { id: string; name: string; location: string; vertical: VerticalId; vertical_label: string; customised: boolean; cameras: AdminCamera[] }
export interface AdminOverview { tenants: AdminTenant[]; auth_required: boolean; secrets_key_custom: boolean; whatsapp_configured: boolean; default_whatsapp_numbers: number; default_webhook: boolean }
export interface CameraConn { camera_id: string; scheme: string; host: string; port: number | null; path: string; username: string; has_password: boolean; enabled: boolean; source: string }
export interface CameraForm extends Omit<CameraConn, "camera_id" | "has_password" | "source"> { password?: string; clear_password?: boolean }
export interface TestResult { ok: boolean; bytes: number; error: string; preview?: string }
const T = (id: string) => `/admin/tenants/${encodeURIComponent(id)}`;
const Q = (cam: string) => (cam ? `?camera=${encodeURIComponent(cam)}` : "");

export const api = {
  adminOverview: () => json<AdminOverview>("/admin/overview"),
  adminConfig: (id: string, cam: string) => json<AdminConfig>(`${T(id)}/config${Q(cam)}`),
  saveAdminConfig: (id: string, cam: string, config: SiteCfg) => json<AdminConfig>(`${T(id)}/config${Q(cam)}`, jsonInit("PUT", { config })),
  resetAdminConfig: (id: string, cam: string) => json<{ ok: true }>(`${T(id)}/config${Q(cam)}`, { method: "DELETE" }),
  adminCamera: (id: string, cam: string) => json<CameraConn>(`${T(id)}/cameras/${encodeURIComponent(cam)}`),
  saveAdminCamera: (id: string, cam: string, b: CameraForm) => json<CameraConn>(`${T(id)}/cameras/${encodeURIComponent(cam)}`, jsonInit("PUT", b)),
  deleteAdminCamera: (id: string, cam: string) => json<{ ok: true }>(`${T(id)}/cameras/${encodeURIComponent(cam)}`, { method: "DELETE" }),
  testAdminCamera: (id: string, cam: string, b: CameraForm | Record<string, never>) => json<TestResult>(`${T(id)}/cameras/${encodeURIComponent(cam)}/test`, jsonInit("POST", b)),
  saveAdminSite: (id: string, b: { name?: string; location?: string }) => json<{ id: string; name: string; location: string }>(`${T(id)}/site`, jsonInit("PUT", b)),
  saveAdminSop: (id: string, text: string) => json<{ ok: true; note: string }>(`${T(id)}/sop`, jsonInit("PUT", { text })),

  health: () => json<Health>("/health"),
  verticals: () => json<Vertical[]>("/verticals"),
  tenants: () => json<Tenant[]>("/tenants"),
  createTenant: (b: NewTenantBody) => json<CreatedTenant>("/tenants", jsonInit("POST", b)),
  alerts: (id: string, limit = 50) => json<Alert[]>(`/tenants/${encodeURIComponent(id)}/alerts?limit=${limit}`),
  events: (id: string, limit = 50) => json<EventRow[]>(`/tenants/${encodeURIComponent(id)}/events?limit=${limit}`),
  actuations: (id: string, limit = 30) => json<Actuation[]>(`/tenants/${encodeURIComponent(id)}/actuations?limit=${limit}`),
  graph: (id: string) => json<GraphData>(`/tenants/${encodeURIComponent(id)}/graph`),
  verticalStats: (v: string) => json<VerticalStats>(`/verticals/${encodeURIComponent(v)}/stats`),
  usage: () => json<Usage>("/usage"),
  ack: (alertId: string, falseAlarm = false) =>
    json<{ ok: true }>(`/alerts/${encodeURIComponent(alertId)}/ack`, jsonInit("POST", { false_alarm: falseAlarm })),
  alertImageBlob: async (alertId: string): Promise<Blob> => {
    const res = await raw(`/alerts/${encodeURIComponent(alertId)}/image`);
    return res.blob();
  },
  notifications: (id: string) => json<NotificationList>(`/tenants/${encodeURIComponent(id)}/notifications?limit=30`),
  markNotificationsRead: (id: string, ids?: string[]) =>
    json<{ changed: number }>(`/tenants/${encodeURIComponent(id)}/notifications/read`, jsonInit("POST", ids ? { ids } : {})),
  feeds: (id: string) => json<FeedInfo[]>(`/tenants/${encodeURIComponent(id)}/feeds`),
  addFeed: (id: string, camera_id: string, url: string) =>
    json<FeedInfo>(`/tenants/${encodeURIComponent(id)}/feeds`, jsonInit("POST", { camera_id, url })),
  removeFeed: (id: string, cam: string) =>
    json<{ ok: true }>(`/tenants/${encodeURIComponent(id)}/feeds/${encodeURIComponent(cam)}`, { method: "DELETE" }),
  cameras: (id: string) => json<CameraInfo[]>(`/tenants/${encodeURIComponent(id)}/cameras`),
  cameraFrameBlob: async (id: string, cam: string): Promise<Blob> => {
    const res = await raw(`/tenants/${encodeURIComponent(id)}/cameras/${encodeURIComponent(cam)}/latest`);
    return res.blob();
  },
  eventImageBlob: async (tenantId: string, frameId: string): Promise<Blob> => {
    const res = await raw(`/tenants/${encodeURIComponent(tenantId)}/events/${encodeURIComponent(frameId)}/image`);
    return res.blob();
  },
  ingest: (tenantId: string, file: File, sensors: string, cameraId: string, force: boolean) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("sensors", sensors);
    fd.append("camera_id", cameraId || "cam_1");
    fd.append("force", String(force));
    return json<IngestResult>(`/ingest/${encodeURIComponent(tenantId)}`, { method: "POST", body: fd });
  },
};
