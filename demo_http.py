"""
Demo Task 2 — Full HTTP: Router client ↔ FastAPI Gateway ↔ WebSocket

Chạy:
  Terminal 1:  uvicorn gateway.main:app --port 8000
  Terminal 2:  python3 demo_http.py

Flow:
  1. Router đăng ký certificate (Task 1 — provision)
  2. Router xác thực challenge-response (Task 1 — auth)
  3. Router gửi telemetry qua HTTP mỗi bước
  4. Billing service xử lý, broadcast WebSocket
  5. Demo client in WS messages real-time
"""
import asyncio
import json
import math
import sys

import aiohttp
import websockets
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, ".")
from device_verification import SimulatedTPM

BASE   = "http://localhost:8000"
WS_URL = "ws://localhost:8000/admin/live-feed"

HOME_HCM  = (10.7769, 106.7009)
DEVICE_ID = "RTR-DEMO-01"
SERIAL    = "VNU2024-DEMO"

STATUS_ICON = {
    "INSIDE":   "✅ INSIDE  ",
    "BUFFER":   "⚠️  BUFFER  ",
    "GRACE":    "🕐 GRACE   ",
    "VIOLATED": "🚫 VIOLATED",
    "ALLOWED":  "🌍 ALLOWED ",
}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def sat_params_for(pos: tuple) -> dict:
    """Tính satellite params để triangulate trả về ≈ pos."""
    lat, lon = pos
    ground_dist = 1500.0 * math.cos(math.radians(45.0))
    dlat = ground_dist / 111.32
    return dict(
        sat_id="20",
        sat_lat=round(lat + dlat, 4),
        sat_lon=lon,
        elevation=45.0,
        azimuth=0.0,
        distance_km=1500.0,
    )


def offset(home: tuple, dist_km: float, bearing: float = 0.0) -> tuple:
    dlat = dist_km * math.cos(math.radians(bearing)) / 111.32
    dlon = dist_km * math.sin(math.radians(bearing)) / (
        111.32 * math.cos(math.radians(home[0]))
    )
    return (round(home[0] + dlat, 6), round(home[1] + dlon, 6))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def register_and_auth(session: aiohttp.ClientSession, tpm: SimulatedTPM) -> str:
    """Đăng ký + xác thực, trả về session_token."""
    pub_pem = tpm.get_public_key_pem()

    # 1. Provision — gateway cấp certificate
    async with session.post(f"{BASE}/provision/register", json={
        "device_id": DEVICE_ID,
        "hardware_serial": SERIAL,
        "public_key_pem": pub_pem.hex(),
    }) as r:
        data = await r.json()
        if r.status != 200:
            print(f"  [ERROR] /provision/register: {data}")
            sys.exit(1)
        cert_hex = data["certificate"]
    print(f"  [Provision] Certificate cấp cho {DEVICE_ID}")

    # 2. Challenge
    async with session.post(f"{BASE}/auth/challenge",
                            json={"device_id": DEVICE_ID}) as r:
        nonce = bytes.fromhex((await r.json())["nonce"])

    # 3. Sign bằng TPM + gửi verify
    payload = nonce + DEVICE_ID.encode()
    sig = tpm.sign(payload)
    async with session.post(f"{BASE}/auth/verify", json={
        "device_id": DEVICE_ID,
        "certificate": cert_hex,
        "signature": sig.hex(),
    }) as r:
        result = await r.json()
        if r.status != 200:
            print(f"  [ERROR] /auth/verify: {result}")
            sys.exit(1)

    token = result["session_token"]
    print(f"  [Auth]      session_token = {token[:20]}...")
    return token


async def set_home(session: aiohttp.ClientSession) -> None:
    async with session.post(f"{BASE}/billing/geofence/{DEVICE_ID}", json={
        "lat": HOME_HCM[0], "lon": HOME_HCM[1], "radius_km": 0.5,
    }) as r:
        await r.json()
    print(f"  [Home]      {HOME_HCM}  bán kính=0.5km  buffer=0.3km")


async def push_telemetry(
    session: aiohttp.ClientSession,
    token: str,
    real_pos: tuple,
    gps: tuple | None = None,
) -> None:
    sp = sat_params_for(real_pos)
    gps_used = gps if gps is not None else real_pos
    async with session.post(f"{BASE}/telemetry/{DEVICE_ID}", json={
        "session_token": token,
        **sp,
        "gps_lat": gps_used[0],
        "gps_lon": gps_used[1],
    }) as r:
        if r.status != 200:
            print(f"  [WARN] telemetry rejected: {await r.json()}")


async def update_plan(session: aiohttp.ClientSession, plan: str) -> None:
    async with session.post(f"{BASE}/billing/plan/{DEVICE_ID}/update",
                            json={"plan_type": plan}) as r:
        await r.json()
    print(f"\n  [Plan] Đổi sang gói {plan}")


# ---------------------------------------------------------------------------
# WebSocket listener (background task)
# ---------------------------------------------------------------------------

async def ws_listener(stop: asyncio.Event) -> None:
    try:
        async with websockets.connect(WS_URL) as ws:
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.4)
                    msg = json.loads(raw)
                    if msg.get("router_id") != DEVICE_ID:
                        continue
                    icon  = STATUS_ICON.get(msg["status"], msg["status"])
                    spoof = "  ⚠️  GPS SPOOF DETECTED" if msg.get("suspicious") else ""
                    print(f"  [WS]  {icon}  dist={msg['dist_km']:.3f}km{spoof}")
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        print(f"  [WS] disconnected: {e}")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

async def scenario_a(session: aiohttp.ClientSession, token: str) -> None:
    """FIXED plan — di chuyển ra ngoài geofence rồi quay về."""
    print("\n" + "=" * 64)
    print("Kịch bản A — Gói FIXED: di chuyển ra ngoài geofence rồi quay về")
    print("=" * 64)

    steps = [
        (offset(HOME_HCM, 0.00), "Tại nhà 0km"),
        (offset(HOME_HCM, 0.30), "Di chuyển 300m"),
        (offset(HOME_HCM, 0.55), "Qua ranh giới → BUFFER 550m"),
        (offset(HOME_HCM, 0.85), "Ngoài buffer → GRACE 850m"),
        (offset(HOME_HCM, 1.20), "Vẫn ngoài 1.2km"),
        (offset(HOME_HCM, 0.40), "Quay về 400m → INSIDE"),
    ]
    for pos, desc in steps:
        print(f"  [TX]  {desc}")
        await push_telemetry(session, token, pos)
        await asyncio.sleep(1.5)


async def scenario_b(session: aiohttp.ClientSession, token: str) -> None:
    """GPS Spoofing detection."""
    print("\n" + "=" * 64)
    print("Kịch bản B — GPS Spoofing detection")
    print("=" * 64)

    HN = (21.0285, 105.8542)

    steps = [
        (HOME_HCM, HOME_HCM, "GPS khớp satellite (HCM) → hợp lệ"),
        (HOME_HCM, HN,       "GPS báo Hà Nội nhưng satellite thấy HCM → SPOOF"),
        (HN,       HOME_HCM, "Thực ở HN, GPS báo HCM → SPOOF"),
        (HN,       HN,       "GPS khớp satellite (HN) → hợp lệ, nhưng xa home"),
    ]
    for real_pos, gps_rep, desc in steps:
        print(f"  [TX]  {desc}")
        await push_telemetry(session, token, real_pos, gps=gps_rep)
        await asyncio.sleep(1.5)


async def scenario_c(session: aiohttp.ClientSession, token: str) -> None:
    """MOBILITY plan — tự do di chuyển."""
    print("\n" + "=" * 64)
    print("Kịch bản C — Gói MOBILITY: tự do di chuyển")
    print("=" * 64)

    waypoints = [
        (HOME_HCM,             "HCM (0km)"),
        ((10.3671, 107.0843),  "Vũng Tàu (~62km)"),
        ((11.9465, 108.4419),  "Đà Lạt (~230km)"),
        ((16.0544, 108.2022),  "Đà Nẵng (~609km)"),
        ((21.0285, 105.8542),  "Hà Nội (~1144km)"),
        ((22.3964, 114.1095),  "Hồng Kông (~1513km)"),
    ]
    for pos, desc in waypoints:
        print(f"  [TX]  {desc}")
        await push_telemetry(session, token, pos)
        await asyncio.sleep(1.5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 64)
    print("DEMO TASK 2 — Full HTTP (Router ↔ Gateway ↔ WebSocket)")
    print("=" * 64)

    # Kiểm tra server
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BASE}/docs") as r:
                assert r.status == 200
    except Exception:
        print(f"\n[ERROR] Không kết nối được {BASE}")
        print("  Chạy trước: uvicorn gateway.main:app --port 8000")
        sys.exit(1)

    tpm = SimulatedTPM()

    async with aiohttp.ClientSession() as session:

        # Phase 1: Đăng ký + xác thực (Task 1)
        print("\n[Phase 1] Task 1 — Đăng ký & Xác thực")
        token = await register_and_auth(session, tpm)
        await set_home(session)

        # Phase 2: Khởi động WS listener
        stop_ws = asyncio.Event()
        ws_task = asyncio.create_task(ws_listener(stop_ws))
        await asyncio.sleep(0.3)   # cho WS kịp connect

        print("\n[Phase 2] Task 2 — Billing & Geofencing (WS = billing service output)")

        # Scenarios
        await scenario_a(session, token)
        await scenario_b(session, token)
        await update_plan(session, "MOBILITY")
        await scenario_c(session, token)

        # Kết thúc
        await asyncio.sleep(1.0)
        stop_ws.set()
        await ws_task

    print("\n" + "=" * 64)
    print("DEMO HOÀN TẤT")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
