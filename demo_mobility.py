"""
Demo Cách 2 — Direct position injection: Router di chuyển theo trajectory.

Không cần re-run Hypatia. Satellite parameters được tính tự động sao cho
triangulate_router_position(sat_params) ≈ vị trí thực của router.

Kịch bản:
  A. FIXED plan  — router đi ra ngoài geofence → VIOLATED → quay về
  B. GPS Spoofing — router báo GPS giả, satellite phát hiện
  C. MOBILITY plan — router đi xa tùy ý, không bị phạt
"""
import asyncio
import math
import time

from device_verification import DeviceDatabase
from billing import (
    GeofenceConfig,
    MockGateway,
    RouterTelemetry,
    SpatioTemporalBillingService,
    haversine,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HOME_HCM = (10.7769, 106.7009)

STATUS_LABEL = {
    "INSIDE":   "✅ Trong vùng",
    "BUFFER":   "⚠️  Vùng đệm ",
    "GRACE":    "🕐 Grace     ",
    "VIOLATED": "🚫 Vi phạm  ",
    "ALLOWED":  "🌍 Di động  ",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sat_params_for(pos: tuple) -> dict:
    """
    Tính satellite parameters sao cho triangulate_router_position() ≈ pos.
    Dùng satellite chính bắc, elevation=45°, distance=1500km.
    """
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


def make_telemetry(real_pos: tuple, gps_reported=None) -> RouterTelemetry:
    """
    Tạo RouterTelemetry với satellite params nhất quán với real_pos.
    gps_reported: None = dùng real_pos, hoặc truyền tọa độ giả để test spoofing.
    """
    return RouterTelemetry(
        **sat_params_for(real_pos),
        timestamp=time.time(),
        gps=gps_reported if gps_reported is not None else real_pos,
    )


def offset(home: tuple, dist_km: float, bearing_deg: float = 0.0) -> tuple:
    """Tạo tọa độ cách home dist_km theo hướng bearing (0=Bắc, 90=Đông)."""
    dlat = dist_km * math.cos(math.radians(bearing_deg)) / 111.32
    dlon = dist_km * math.sin(math.radians(bearing_deg)) / (
        111.32 * math.cos(math.radians(home[0]))
    )
    return (round(home[0] + dlat, 6), round(home[1] + dlon, 6))


def make_env(router_id: str, home: tuple, grace_s: int = 5, radius_km: float = 0.5):
    db = DeviceDatabase()
    db.add(router_id, f"SN-{router_id}")
    db.set_home(router_id, *home, radius_km=radius_km)

    gw = MockGateway(db)
    gw.active_sessions[f"sess-{router_id}"] = router_id

    cfg = GeofenceConfig(
        allowed_radius_km=radius_km,
        buffer_zone_km=0.3,
        grace_period_s=grace_s,
    )
    svc = SpatioTemporalBillingService(gw, cfg)
    svc.enforcer.grace_period_s = grace_s
    return db, gw, svc


async def tick(svc, gw, router_id, real_pos, step, desc, gps_reported=None):
    """Inject 1 telemetry frame rồi process."""
    gw.set_telemetry(router_id, make_telemetry(real_pos, gps_reported))
    prev_n = len(gw.notifications)

    await svc._process_router(router_id)

    ws = gw.admin_ws.messages
    last = ws[-1] if ws else {}
    new_notifs = gw.notifications[prev_n:]

    status  = last.get("status", "?")
    dist    = last.get("dist_km", 0.0)
    spoof   = last.get("suspicious", False)
    bw      = gw.bandwidth.get(router_id, 1.0)
    label   = STATUS_LABEL.get(status, status)
    spoof_s = "  ⚠️  GPS SPOOF DETECTED" if spoof else ""
    bw_s    = f"  BW={bw:.1f}x" if bw < 1.0 else ""
    notif_s = f"  [{new_notifs[-1]['type']}]" if new_notifs else ""

    print(f"  t={step:2d}s  {desc:38s}  {dist:.3f}km  {label}{spoof_s}{bw_s}{notif_s}")
    return status


# ---------------------------------------------------------------------------
# Scenario A — FIXED plan: di chuyển ra → VIOLATED → quay về
# ---------------------------------------------------------------------------
async def scenario_a():
    print("\n" + "=" * 72)
    print("Kịch bản A: Gói FIXED — di chuyển ra ngoài geofence rồi quay về")
    print(f"  Home: {HOME_HCM}  |  Bán kính: 0.5 km  |  Buffer: 0.3 km  |  Grace: 5s")
    print("=" * 72)

    RID = "RTR-HCM-A"
    _, gw, svc = make_env(RID, HOME_HCM, grace_s=5)

    trajectory = [
        (offset(HOME_HCM, 0.00), "Tại nhà"),
        (offset(HOME_HCM, 0.20), "Di chuyển ~200m"),
        (offset(HOME_HCM, 0.45), "Gần ranh giới ~450m"),
        (offset(HOME_HCM, 0.55), "Qua ranh giới → vùng đệm ~550m"),
        (offset(HOME_HCM, 0.72), "Vùng đệm ~720m"),
        (offset(HOME_HCM, 0.85), "Vào vùng ngoài buffer ~850m"),
        (offset(HOME_HCM, 1.10), "Tiếp tục ra xa ~1100m"),
        (offset(HOME_HCM, 1.40), "Vẫn ngoài vùng ~1400m"),
    ]

    for step, (pos, desc) in enumerate(trajectory):
        await tick(svc, gw, RID, pos, step, desc)

    # Giả lập grace period đã hết
    print(f"\n  ── Grace period 5s trôi qua ──\n")
    if RID in svc.monitor.violation_starts:
        svc.monitor.violation_starts[RID] = time.time() - 6

    await tick(svc, gw, RID, offset(HOME_HCM, 1.40), 8, "Hết grace → VIOLATED")
    print(f"\n  → Disconnected: {RID in gw.disconnected}")
    print(f"  → Violations logged: {len(gw.device_db.get_violations(10))}")

    # Quay về
    print(f"\n  ── Router quay về nhà ──\n")
    gw.disconnected.discard(RID)
    gw.active_sessions[f"sess-{RID}"] = RID
    svc.monitor.violation_starts.pop(RID, None)

    return_traj = [
        (offset(HOME_HCM, 0.90), "Quay về ~900m"),
        (offset(HOME_HCM, 0.45), "Quay về ~450m"),
        (offset(HOME_HCM, 0.05), "Về đến nhà"),
    ]
    for step, (pos, desc) in enumerate(return_traj, start=9):
        await tick(svc, gw, RID, pos, step, desc)

    print(f"\n  → Bandwidth khôi phục: {gw.bandwidth.get(RID, 1.0):.1f}x")


# ---------------------------------------------------------------------------
# Scenario B — GPS Spoofing detection
# ---------------------------------------------------------------------------
async def scenario_b():
    print("\n" + "=" * 72)
    print("Kịch bản B: Phát hiện GPS Spoofing")
    print("  Satellite tính vị trí độc lập, so sánh với GPS router báo cáo")
    print("=" * 72)

    RID = "RTR-SPF-B"
    _, gw, svc = make_env(RID, HOME_HCM, grace_s=5)

    HN = (21.0285, 105.8542)

    steps = [
        # (real_pos, gps_reported, desc)
        (HOME_HCM, HOME_HCM, "GPS khớp satellite (HCM) → hợp lệ"),
        (HOME_HCM, HN,       "GPS báo Hà Nội nhưng satellite thấy HCM"),
        (HN,       HOME_HCM, "Thực ở HN, GPS báo HCM — satellite phát hiện"),
        (HN,       HN,       "GPS khớp satellite (HN) → hợp lệ, nhưng ngoài geofence"),
    ]

    for step, (real_pos, gps_rep, desc) in enumerate(steps):
        await tick(svc, gw, RID, real_pos, step, desc, gps_reported=gps_rep)


# ---------------------------------------------------------------------------
# Scenario C — MOBILITY plan
# ---------------------------------------------------------------------------
async def scenario_c():
    print("\n" + "=" * 72)
    print("Kịch bản C: Gói MOBILITY — tự do di chuyển, không bị geofence")
    print("=" * 72)

    RID = "RTR-MOB-C"
    db, gw, svc = make_env(RID, HOME_HCM, grace_s=5)
    db.update_plan(RID, "MOBILITY")

    waypoints = [
        (HOME_HCM,              "Xuất phát HCM"),
        ((10.3671, 107.0843),   "Vũng Tàu (~85km)"),
        ((11.9465, 108.4419),   "Đà Lạt (~265km)"),
        ((16.0544, 108.2022),   "Đà Nẵng (~900km)"),
        ((21.0285, 105.8542),   "Hà Nội (~1140km)"),
        ((22.3964, 114.1095),   "Hồng Kông (~2100km)"),
    ]

    for step, (pos, desc) in enumerate(waypoints):
        dist_from_home = haversine(*pos, *HOME_HCM)
        await tick(svc, gw, RID, pos, step, f"{desc} ({dist_from_home:.0f}km từ nhà)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    await scenario_a()
    await scenario_b()
    await scenario_c()
    print("\n" + "=" * 72)
    print("DEMO HOÀN TẤT")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
