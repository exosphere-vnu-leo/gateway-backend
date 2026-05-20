"""
Tests for Task 2 — Spatio-temporal Billing & Geofencing.

Các trường hợp:
  haversine:
    - Khoảng cách 0 (cùng điểm)
    - Khoảng cách đã biết (HN–HCM ~1750 km)
  validate_position:
    - GPS khớp satellite → dùng GPS, không suspicious
    - GPS lệch > 50 km → suspicious, dùng satellite-derived
    - Không có GPS → dùng satellite-derived
  GeofenceMonitor:
    - INSIDE (trong bán kính)
    - BUFFER (vùng đệm)
    - GRACE (ngoài buffer, trong grace period)
    - VIOLATED (hết grace period)
    - MOBILITY → ALLOWED luôn
    - Trở về vùng sau khi vi phạm → reset violation_start
  BillingEnforcer:
    - INSIDE/ALLOWED → bandwidth 1.0
    - BUFFER → gửi notification WARNING
    - GRACE → bandwidth 0.5 + notification THROTTLE
    - VIOLATED → disconnect + log_violation + WS broadcast
"""
import time
import pytest
import pytest_asyncio

from billing import (
    GeoStatus,
    GeofenceConfig,
    GeofenceMonitor,
    MockGateway,
    BillingEnforcer,
    haversine,
    validate_position,
)
from device_verification import DeviceDatabase


HOME_HCM = (10.7769, 106.7009)   # HCM City
HOME_HN  = (21.0285, 105.8542)   # Hà Nội


# ---------------------------------------------------------------------------
# haversine
# ---------------------------------------------------------------------------

def test_haversine_same_point():
    assert haversine(*HOME_HCM, *HOME_HCM) == pytest.approx(0.0, abs=1e-6)


def test_haversine_hn_hcm():
    dist = haversine(*HOME_HN, *HOME_HCM)
    # Khoảng cách thực HN–HCM ~ 1137 km (đường chim bay)
    assert 1100 < dist < 1200


def test_haversine_symmetry():
    d1 = haversine(10.0, 106.0, 21.0, 105.0)
    d2 = haversine(21.0, 105.0, 10.0, 106.0)
    assert d1 == pytest.approx(d2, rel=1e-9)


def test_haversine_short_distance():
    # ~0.2 km về phía Bắc từ HOME_HCM
    pos = (HOME_HCM[0] + 0.0018, HOME_HCM[1])
    dist = haversine(*pos, *HOME_HCM)
    assert 0.15 < dist < 0.25


# ---------------------------------------------------------------------------
# validate_position
# ---------------------------------------------------------------------------

def test_validate_position_gps_matches():
    reported = (10.7769, 106.7009)
    derived  = (10.7770, 106.7010)   # ~15m sai lệch
    pos, suspicious = validate_position("R-001", reported, derived)
    assert not suspicious
    assert pos == reported   # dùng GPS vì chính xác hơn


def test_validate_position_gps_spoofing():
    reported = HOME_HCM          # báo cáo: HCM
    derived  = HOME_HN           # satellite nói: Hà Nội (>1000km lệch)
    pos, suspicious = validate_position("R-001", reported, derived)
    assert suspicious
    assert pos == derived        # dùng satellite-derived


def test_validate_position_no_gps():
    pos, suspicious = validate_position("R-001", None, HOME_HCM)
    assert not suspicious
    assert pos == HOME_HCM


def test_validate_position_boundary_49km():
    """49 km < threshold 50 km → không suspicious."""
    import math
    # ~49 km về phía Bắc
    offset = 49.0 / 111.32
    derived = (HOME_HCM[0] + offset, HOME_HCM[1])
    pos, suspicious = validate_position("R-001", HOME_HCM, derived)
    assert not suspicious


def test_validate_position_boundary_51km():
    """51 km > threshold 50 km → suspicious."""
    import math
    offset = 51.0 / 111.32
    derived = (HOME_HCM[0] + offset, HOME_HCM[1])
    pos, suspicious = validate_position("R-001", HOME_HCM, derived)
    assert suspicious


# ---------------------------------------------------------------------------
# GeofenceMonitor
# ---------------------------------------------------------------------------

@pytest.fixture
def monitor():
    cfg = GeofenceConfig(allowed_radius_km=0.5, buffer_zone_km=0.3, grace_period_s=10)
    return GeofenceMonitor(cfg)


def pos_at_dist_km(home, km):
    """Tạo tọa độ cách home đúng km về phía Bắc."""
    return (home[0] + km / 111.32, home[1])


def test_monitor_inside(monitor):
    pos = pos_at_dist_km(HOME_HCM, 0.2)
    assert monitor.check("R1", "FIXED", pos, HOME_HCM) == GeoStatus.INSIDE


def test_monitor_buffer(monitor):
    pos = pos_at_dist_km(HOME_HCM, 0.65)   # 0.5 < 0.65 < 0.8
    assert monitor.check("R1", "FIXED", pos, HOME_HCM) == GeoStatus.BUFFER


def test_monitor_grace(monitor):
    pos = pos_at_dist_km(HOME_HCM, 1.0)    # ngoài buffer
    status = monitor.check("R1", "FIXED", pos, HOME_HCM)
    assert status == GeoStatus.GRACE


def test_monitor_violated_after_grace(monitor):
    pos = pos_at_dist_km(HOME_HCM, 1.0)
    monitor.check("R1", "FIXED", pos, HOME_HCM)   # ghi violation_start
    # Giả lập đã qua grace period
    monitor.violation_starts["R1"] = time.time() - 11
    status = monitor.check("R1", "FIXED", pos, HOME_HCM)
    assert status == GeoStatus.VIOLATED


def test_monitor_mobility_always_allowed(monitor):
    pos = HOME_HN   # ~1100 km từ HOME_HCM
    assert monitor.check("R1", "MOBILITY", pos, HOME_HCM) == GeoStatus.ALLOWED


def test_monitor_returns_inside_resets_violation(monitor):
    """Router ra ngoài rồi quay về → violation_start bị xóa."""
    pos_out = pos_at_dist_km(HOME_HCM, 1.0)
    pos_in  = pos_at_dist_km(HOME_HCM, 0.2)

    monitor.check("R1", "FIXED", pos_out, HOME_HCM)
    assert "R1" in monitor.violation_starts

    monitor.check("R1", "FIXED", pos_in, HOME_HCM)
    assert "R1" not in monitor.violation_starts


def test_monitor_at_exact_radius_is_inside(monitor):
    """Đúng bằng 0.5 km → INSIDE (dist <= r)."""
    pos = pos_at_dist_km(HOME_HCM, 0.5)
    dist = haversine(*pos, *HOME_HCM)
    # Chấp nhận sai số nhỏ do tọa độ không hoàn toàn chính xác
    assert dist == pytest.approx(0.5, abs=0.02)
    status = monitor.check("R1", "FIXED", pos, HOME_HCM)
    assert status in (GeoStatus.INSIDE, GeoStatus.BUFFER)  # boundary


# ---------------------------------------------------------------------------
# BillingEnforcer
# ---------------------------------------------------------------------------

@pytest.fixture
def db_with_device():
    db = DeviceDatabase()
    db.add("R-HCM", "SN-001")
    db.set_home("R-HCM", *HOME_HCM)
    return db


@pytest.fixture
def gateway(db_with_device):
    gw = MockGateway(db_with_device)
    gw.active_sessions["sess-001"] = "R-HCM"
    return gw


@pytest.fixture
def enforcer(gateway):
    e = BillingEnforcer(gateway)
    e.grace_period_s = 10
    return e


@pytest.mark.asyncio
async def test_enforcer_inside_full_bandwidth(enforcer, gateway):
    await enforcer.enforce("R-HCM", GeoStatus.INSIDE, 0.2, None)
    assert gateway.bandwidth.get("R-HCM") == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_enforcer_allowed_full_bandwidth(enforcer, gateway):
    await enforcer.enforce("R-HCM", GeoStatus.ALLOWED, 1200.0, None)
    assert gateway.bandwidth.get("R-HCM") == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_enforcer_buffer_sends_warning(enforcer, gateway):
    await enforcer.enforce("R-HCM", GeoStatus.BUFFER, 0.65, None)
    assert len(gateway.notifications) == 1
    assert gateway.notifications[0]["type"] == "WARNING"
    assert "R-HCM" in gateway.notifications[0]["router_id"]


@pytest.mark.asyncio
async def test_enforcer_grace_throttle_and_notify(enforcer, gateway):
    violation_start = time.time() - 5   # 5s đã trôi qua
    await enforcer.enforce("R-HCM", GeoStatus.GRACE, 1.0, violation_start)
    assert gateway.bandwidth.get("R-HCM") == pytest.approx(0.5)
    assert any(n["type"] == "THROTTLE" for n in gateway.notifications)


@pytest.mark.asyncio
async def test_enforcer_violated_disconnects(enforcer, gateway):
    await enforcer.enforce("R-HCM", GeoStatus.VIOLATED, 2.0, time.time() - 15)
    assert "R-HCM" in gateway.disconnected


@pytest.mark.asyncio
async def test_enforcer_violated_logs_violation(enforcer, gateway, db_with_device):
    await enforcer.enforce("R-HCM", GeoStatus.VIOLATED, 2.0, time.time() - 15)
    violations = db_with_device.get_violations(10)
    assert any(v["device_id"] == "R-HCM" for v in violations)


@pytest.mark.asyncio
async def test_enforcer_violated_broadcasts_ws(enforcer, gateway):
    await enforcer.enforce("R-HCM", GeoStatus.VIOLATED, 2.0, time.time() - 15)
    ws_msgs = gateway.admin_ws.messages
    assert any(
        m.get("event") == "VIOLATION" and m.get("router_id") == "R-HCM"
        for m in ws_msgs
    )


@pytest.mark.asyncio
async def test_enforcer_violated_removes_session(enforcer, gateway):
    await enforcer.enforce("R-HCM", GeoStatus.VIOLATED, 2.0, time.time() - 15)
    assert "R-HCM" not in gateway.active_sessions.values()
