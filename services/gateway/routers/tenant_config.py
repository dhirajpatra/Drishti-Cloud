from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

# Define the request body model
class TenantConfigUpdate(BaseModel):
    thresholds: Optional[Dict[str, Any]] = None
    notifications: Optional[Dict[str, Any]] = None
    cameras_config: Optional[List[Dict[str, Any]]] = None

# GET Endpoint: Fetch configurations for a given tenant
@app.get("/api/tenants/{tenant_id}/config")
async def get_tenant_config(tenant_id: str):
    # Fetch from Postgres DB
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT thresholds, notifications, cameras_config FROM tenant_configs WHERE tenant_id = $1",
            tenant_id
        )
        if not row:
            # Fallback default values
            return {
                "thresholds": {"unattended_bag_sec": 120, "no_motion_sec": 7200, "crowd_threshold": 50},
                "notifications": {"webhook_url": ""},
                "cameras_config": []
            }
        return {
            "thresholds": json.loads(row["thresholds"]),
            "notifications": json.loads(row["notifications"]),
            "cameras_config": json.loads(row["cameras_config"])
        }

# PUT Endpoint: Upsert configurations for a given tenant
@app.put("/api/tenants/{tenant_id}/config")
async def update_tenant_config(tenant_id: str, payload: TenantConfigUpdate):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO tenant_configs (tenant_id, thresholds, notifications, cameras_config, updated_at)
            VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id) DO UPDATE SET
                thresholds = COALESCE(EXCLUDED.thresholds, tenant_configs.thresholds),
                notifications = COALESCE(EXCLUDED.notifications, tenant_configs.notifications),
                cameras_config = COALESCE(EXCLUDED.cameras_config, tenant_configs.cameras_config),
                updated_at = CURRENT_TIMESTAMP
        """, tenant_id, json.dumps(payload.thresholds or {}), json.dumps(payload.notifications or {}), json.dumps(payload.cameras_config or []))
    return {"status": "ok", "message": f"Config updated for {tenant_id}"}