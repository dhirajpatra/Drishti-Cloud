-- Creates table for tenant-level dynamic settings
CREATE TABLE IF NOT EXISTS tenant_configs (
    tenant_id VARCHAR(64) PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    thresholds JSONB DEFAULT '{
        "unattended_bag_sec": 120,
        "no_motion_sec": 7200,
        "staff_absent_sec": 3600,
        "crowd_threshold": 50,
        "night_start_hour": 22,
        "night_end_hour": 5,
        "alert_cooldown_sec": 300
    }'::jsonb,
    notifications JSONB DEFAULT '{
        "whatsapp_recipients": [],
        "webhook_url": "",
        "notify_on_warning": true,
        "notify_on_critical": true
    }'::jsonb,
    cameras_config JSONB DEFAULT '[]'::jsonb,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);