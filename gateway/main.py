"""
VNU-LEO Advanced Gateway — FastAPI server tích hợp Task 1 + Task 2.
"""
import asyncio
import sys
from pathlib import Path

# Resolve imports khi chạy trực tiếp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from device_verification import (
    DeviceDatabase,
    GatewayAuthService,
    VNULEOCertificateAuthority,
)
from billing import GeofenceConfig, SpatioTemporalBillingService
from billing.enforcer import MockGateway

# ---------------------------------------------------------------------------
# App & shared state
# ---------------------------------------------------------------------------
app = FastAPI(title="VNU-LEO Advanced Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ca = VNULEOCertificateAuthority()
device_db = DeviceDatabase()
gateway = MockGateway(device_db)
auth_service = GatewayAuthService(ca, device_db)
billing_svc = SpatioTemporalBillingService(gateway)


@app.on_event("startup")
async def startup():
    asyncio.create_task(billing_svc.run())


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ProvisionRequest(BaseModel):
    device_id: str
    hardware_serial: str
    public_key_pem: str    # hex-encoded PEM bytes


class ChallengeRequest(BaseModel):
    device_id: str


class VerifyRequest(BaseModel):
    device_id: str
    certificate: str       # hex-encoded PEM bytes
    signature: str         # hex-encoded bytes


class PlanUpdateRequest(BaseModel):
    plan_type: str         # "FIXED" | "MOBILITY"


class HomeRequest(BaseModel):
    lat: float
    lon: float
    radius_km: float = 0.5


class TelemetryRequest(BaseModel):
    session_token: str
    sat_id: str
    sat_lat: float
    sat_lon: float
    elevation: float
    azimuth: float
    distance_km: float
    gps_lat: float | None = None
    gps_lon: float | None = None


# ---------------------------------------------------------------------------
# Task 1 — Provisioning endpoints
# ---------------------------------------------------------------------------
@app.post("/provision/register")
async def register_device(req: ProvisionRequest):
    """Cấp certificate cho Router mới."""
    cert = ca.issue_certificate(
        req.device_id,
        req.hardware_serial,
        bytes.fromhex(req.public_key_pem),
    )
    device_db.add(req.device_id, req.hardware_serial)
    return {"certificate": cert.public_bytes(serialization.Encoding.PEM).hex()}


@app.get("/provision/ca-cert")
async def get_ca_cert():
    return {"ca_cert": ca.get_ca_cert_pem().hex()}


@app.delete("/provision/revoke/{device_id}")
async def revoke_device(device_id: str):
    ca.revoke(device_id)
    return {"revoked": device_id}


# ---------------------------------------------------------------------------
# Task 1 — Authentication endpoints
# ---------------------------------------------------------------------------
@app.post("/auth/challenge")
async def challenge(req: ChallengeRequest):
    nonce = auth_service.issue_challenge(req.device_id)
    return {"nonce": nonce.hex()}


@app.post("/auth/verify")
async def verify(req: VerifyRequest):
    result = auth_service.verify_response(
        req.device_id,
        bytes.fromhex(req.certificate),
        bytes.fromhex(req.signature),
    )
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["reason"])
    # Activate device session for billing
    gateway.active_sessions[result["session_token"]] = req.device_id
    return result


# ---------------------------------------------------------------------------
# Task 2 — Billing endpoints
# ---------------------------------------------------------------------------
@app.get("/billing/plan/{device_id}")
async def get_plan(device_id: str):
    plan = device_db.get_plan(device_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="device not found")
    return {
        "device_id": plan.device_id,
        "plan_type": plan.plan_type,
        "home_lat": plan.home_lat,
        "home_lon": plan.home_lon,
        "home_radius_km": plan.home_radius_km,
    }


@app.post("/billing/plan/{device_id}/update")
async def update_plan(device_id: str, req: PlanUpdateRequest):
    if not device_db.exists(device_id):
        raise HTTPException(status_code=404, detail="device not found")
    device_db.update_plan(device_id, req.plan_type)
    return {"updated": device_id, "new_plan": req.plan_type}


@app.post("/billing/geofence/{device_id}")
async def set_home_coords(device_id: str, req: HomeRequest):
    if not device_db.exists(device_id):
        raise HTTPException(status_code=404, detail="device not found")
    device_db.set_home(device_id, req.lat, req.lon, req.radius_km)
    return {"device_id": device_id, "home": (req.lat, req.lon), "radius_km": req.radius_km}


@app.get("/billing/violations")
async def get_violations(limit: int = 50):
    return device_db.get_violations(limit)


# ---------------------------------------------------------------------------
# Task 2 — Router telemetry push
# ---------------------------------------------------------------------------
@app.post("/telemetry/{device_id}")
async def push_telemetry(device_id: str, req: TelemetryRequest):
    """Router gửi telemetry lên gateway mỗi giây."""
    from billing import RouterTelemetry
    import time as _time

    owner = auth_service.validate_session(req.session_token)
    if owner != device_id:
        raise HTTPException(status_code=401, detail="invalid session")

    gps = (req.gps_lat, req.gps_lon) if req.gps_lat is not None else None
    telemetry = RouterTelemetry(
        sat_id=req.sat_id,
        sat_lat=req.sat_lat,
        sat_lon=req.sat_lon,
        elevation=req.elevation,
        azimuth=req.azimuth,
        distance_km=req.distance_km,
        timestamp=_time.time(),
        gps=gps,
    )
    gateway.set_telemetry(device_id, telemetry)
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket — realtime feed cho SvelteKit admin
# ---------------------------------------------------------------------------
@app.websocket("/admin/live-feed")
async def live_feed(ws: WebSocket):
    await ws.accept()
    idx = len(gateway.admin_ws.messages)
    try:
        while True:
            # Push new messages accumulated by billing service
            new = gateway.admin_ws.messages[idx:]
            for msg in new:
                await ws.send_json(msg)
            idx += len(new)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
