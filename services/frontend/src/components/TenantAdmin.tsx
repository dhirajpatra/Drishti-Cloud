import { useState, useEffect } from "react";
import { Save, Shield, Bell } from "lucide-react";
import { Panel, btnPrimary, inputCls, inputStyle } from "./ui";

interface TenantAdminProps {
  tenantId: string;
}

export function TenantAdmin({ tenantId }: TenantAdminProps) {
  const [config, setConfig] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/tenants/${tenantId}/config`)
      .then((res) => res.json())
      .then((data) => setConfig(data))
      .catch(() => setMsg("Failed to load configurations"));
  }, [tenantId]);

  const handleSave = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const res = await fetch(`/api/tenants/${tenantId}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (res.ok) setMsg("Configurations saved successfully!");
      else setMsg("Error saving configuration.");
    } catch {
      setMsg("Failed to connect to gateway.");
    } finally {
      setSaving(false);
    }
  };

  if (!config) return <div className="p-4 mono text-xs">Loading configuration...</div>;

  return (
    <Panel kicker="ADMIN" title={`Tenant Settings — ${tenantId}`}>
      {msg && <div className="mb-3 text-xs mono p-2 rounded bg-opacity-20 bg-blue-500">{msg}</div>}

      <div className="space-y-6">
        <div>
          <h4 className="mono text-xs font-semibold mb-2 flex items-center gap-1.5" style={{ color: "#fbbf24" }}>
            <Shield size={14} /> ALERT THRESHOLDS
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <label className="block">
              <span className="text-[11px] text-muted">No Motion Timeout (sec)</span>
              <input
                type="number"
                className={inputCls}
                style={inputStyle}
                value={config.thresholds?.no_motion_sec || 7200}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    thresholds: { ...config.thresholds, no_motion_sec: parseInt(e.target.value) },
                  })
                }
              />
            </label>
            <label className="block">
              <span className="text-[11px] text-muted">Unattended Bag Timeout (sec)</span>
              <input
                type="number"
                className={inputCls}
                style={inputStyle}
                value={config.thresholds?.unattended_bag_sec || 120}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    thresholds: { ...config.thresholds, unattended_bag_sec: parseInt(e.target.value) },
                  })
                }
              />
            </label>
          </div>
        </div>

        <div>
          <h4 className="mono text-xs font-semibold mb-2 flex items-center gap-1.5" style={{ color: "#34d399" }}>
            <Bell size={14} /> NOTIFICATION CHANNELS
          </h4>
          <div className="space-y-2 text-xs">
            <label className="block">
              <span className="text-[11px] text-muted">Webhook Alert URL</span>
              <input
                type="url"
                className={inputCls}
                style={inputStyle}
                placeholder="https://hooks.slack.com/services/..."
                value={config.notifications?.webhook_url || ""}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    notifications: { ...config.notifications, webhook_url: e.target.value },
                  })
                }
              />
            </label>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={saving}
          className="mono text-xs px-4 py-2 rounded-lg flex items-center gap-2"
          style={btnPrimary}
        >
          <Save size={14} /> {saving ? "SAVING..." : "SAVE CONFIGURATION"}
        </button>
      </div>
    </Panel>
  );
}