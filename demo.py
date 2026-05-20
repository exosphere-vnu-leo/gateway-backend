"""
Demo kịch bản cho hội đồng — chạy: python demo.py

Task 1: Device Verification (3 kịch bản)
Task 2: Spatio-temporal Billing (3 kịch bản)
"""
import asyncio
import time
from cryptography.hazmat.primitives import serialization

from device_verification import (
    SimulatedTPM,
    VNULEOCertificateAuthority,
    DeviceDatabase,
    GatewayAuthService,
)
from billing import (
    GeoStatus,
    GeofenceConfig,
    GeofenceMonitor,
    MockGateway,
    BillingEnforcer,
    haversine,
    validate_position,
)


# ─────────────────────────────────────────────────────────────────────────────
# TASK 1 — Device Verification
# ─────────────────────────────────────────────────────────────────────────────

def demo_task1():
    print("=" * 60)
    print("TASK 1 — Device Verification & Anti-Spoofing")
    print("=" * 60)

    ca = VNULEOCertificateAuthority()
    db = DeviceDatabase()
    auth = GatewayAuthService(ca, db)

    # ── Kịch bản 1: Router hợp lệ ──────────────────────────────────────────
    print("\n[Kịch bản 1] Router hợp lệ")
    tpm = SimulatedTPM()
    cert = ca.issue_certificate("RTR-HN-042", "VNU2024XXXX", tpm.get_public_key_pem())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    db.add("RTR-HN-042", "VNU2024XXXX")

    nonce = auth.issue_challenge("RTR-HN-042")
    sig = tpm.sign(nonce + b"RTR-HN-042")
    result = auth.verify_response("RTR-HN-042", cert_pem, sig)
    print(f"  Kết quả: {result}")
    assert result["success"], "Kịch bản 1 FAILED"

    # ── Kịch bản 2: Fake router không có TPM ───────────────────────────────
    print("\n[Kịch bản 2] Fake router — TPM khác, key khác")
    fake_tpm = SimulatedTPM()
    nonce2 = auth.issue_challenge("RTR-HN-042")
    fake_sig = fake_tpm.sign(nonce2 + b"RTR-HN-042")
    result2 = auth.verify_response("RTR-HN-042", cert_pem, fake_sig)
    print(f"  Kết quả: {result2}")
    assert not result2["success"], "Kịch bản 2 FAILED"

    # ── Kịch bản 3: Replay attack — nonce cũ đã hết hạn ───────────────────
    print("\n[Kịch bản 3] Replay attack — nonce hết hạn (giả lập)")
    nonce3 = auth.issue_challenge("RTR-HN-042")
    sig3 = tpm.sign(nonce3 + b"RTR-HN-042")
    # Giả lập nonce đã quá hạn bằng cách override timestamp
    auth.pending_nonces["RTR-HN-042"] = (nonce3, time.time() - 31)
    result3 = auth.verify_response("RTR-HN-042", cert_pem, sig3)
    print(f"  Kết quả: {result3}")
    assert not result3["success"], "Kịch bản 3 FAILED"

    # ── Kịch bản 4: Revoke device ──────────────────────────────────────────
    print("\n[Kịch bản 4] Revoke thiết bị — certificate bị thu hồi")
    ca.revoke("RTR-HN-042")
    nonce4 = auth.issue_challenge("RTR-HN-042")
    sig4 = tpm.sign(nonce4 + b"RTR-HN-042")
    result4 = auth.verify_response("RTR-HN-042", cert_pem, sig4)
    print(f"  Kết quả: {result4}")
    assert not result4["success"], "Kịch bản 4 FAILED"

    print("\n[Task 1] Tất cả kịch bản PASS ✓")


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2 — Spatio-temporal Billing
# ─────────────────────────────────────────────────────────────────────────────

async def demo_task2():
    print("\n" + "=" * 60)
    print("TASK 2 — Spatio-temporal Billing & Geofencing")
    print("=" * 60)

    db = DeviceDatabase()
    db.add("RTR-HCM-001", "VNU2024YYYY")
    db.set_home("RTR-HCM-001", lat=10.7769, lon=106.7009, radius_km=0.5)

    gateway = MockGateway(db)
    gateway.active_sessions["sess-001"] = "RTR-HCM-001"

    config = GeofenceConfig(allowed_radius_km=0.5, buffer_zone_km=0.3, grace_period_s=5)
    monitor = GeofenceMonitor(config)
    enforcer = BillingEnforcer(gateway)
    enforcer.grace_period_s = 5

    home = (10.7769, 106.7009)

    # ── Kịch bản A: Trong vùng phủ ─────────────────────────────────────────
    print("\n[Kịch bản A] Router trong vùng phủ (0.2 km)")
    pos_inside = (10.7787, 106.7009)   # ~0.2 km về phía Bắc
    dist_a = haversine(*pos_inside, *home)
    status_a = monitor.check("RTR-HCM-001", "FIXED", pos_inside, home)
    await enforcer.enforce("RTR-HCM-001", status_a, dist_a, None)
    print(f"  Khoảng cách: {dist_a:.3f} km — Trạng thái: {status_a.value}")
    print(f"  Bandwidth factor: {gateway.bandwidth.get('RTR-HCM-001', 1.0):.2f}")
    assert status_a == GeoStatus.INSIDE

    # ── Kịch bản B: Vùng đệm (buffer) ──────────────────────────────────────
    print("\n[Kịch bản B] Router vùng đệm (0.65 km)")
    pos_buffer = (10.7828, 106.7009)   # ~0.65 km về phía Bắc
    dist_b = haversine(*pos_buffer, *home)
    status_b = monitor.check("RTR-HCM-001", "FIXED", pos_buffer, home)
    await enforcer.enforce("RTR-HCM-001", status_b, dist_b, None)
    print(f"  Khoảng cách: {dist_b:.3f} km — Trạng thái: {status_b.value}")
    print(f"  Notification: {gateway.notifications[-1] if gateway.notifications else 'none'}")
    assert status_b == GeoStatus.BUFFER

    # ── Kịch bản C: Grace period ────────────────────────────────────────────
    print("\n[Kịch bản C] Router ngoài vùng phủ — grace period")
    pos_out = (10.7950, 106.7009)      # ~2 km
    dist_c = haversine(*pos_out, *home)
    status_c = monitor.check("RTR-HCM-001", "FIXED", pos_out, home)
    await enforcer.enforce("RTR-HCM-001", status_c, dist_c,
                           monitor.violation_starts.get("RTR-HCM-001"))
    print(f"  Khoảng cách: {dist_c:.3f} km — Trạng thái: {status_c.value}")
    print(f"  Bandwidth factor: {gateway.bandwidth.get('RTR-HCM-001', 1.0):.2f}")
    assert status_c == GeoStatus.GRACE

    # ── Kịch bản D: VIOLATED — sau grace period ─────────────────────────────
    print("\n[Kịch bản D] Hết grace period — kết nối bị ngắt")
    # Giả lập violation_start đã qua 6 giây (> grace_period_s=5)
    monitor.violation_starts["RTR-HCM-001"] = time.time() - 6
    status_d = monitor.check("RTR-HCM-001", "FIXED", pos_out, home)
    await enforcer.enforce("RTR-HCM-001", status_d, dist_c,
                           monitor.violation_starts.get("RTR-HCM-001"))
    print(f"  Khoảng cách: {dist_c:.3f} km — Trạng thái: {status_d.value}")
    print(f"  Disconnected: {'RTR-HCM-001' in gateway.disconnected}")
    assert status_d == GeoStatus.VIOLATED
    assert "RTR-HCM-001" in gateway.disconnected

    # ── Kịch bản E: Gói MOBILITY — không giới hạn ──────────────────────────
    print("\n[Kịch bản E] Gói MOBILITY — không bị geofence")
    db.update_plan("RTR-HCM-001", "MOBILITY")
    gateway.disconnected.discard("RTR-HCM-001")
    monitor.violation_starts.pop("RTR-HCM-001", None)
    pos_far = (21.0285, 105.8542)      # Hà Nội
    dist_e = haversine(*pos_far, *home)
    status_e = monitor.check("RTR-HCM-001", "MOBILITY", pos_far, home)
    print(f"  Khoảng cách: {dist_e:.1f} km — Trạng thái: {status_e.value}")
    assert status_e == GeoStatus.ALLOWED

    # ── Kịch bản F: GPS Spoofing detection ─────────────────────────────────
    print("\n[Kịch bản F] Phát hiện GPS Spoofing")
    reported_gps = (10.7769, 106.7009)   # Báo cáo: HCM
    derived_pos  = (21.0285, 105.8542)   # Satellite-derived: Hà Nội (~1750 km)
    pos_used, suspicious = validate_position("RTR-HCM-001", reported_gps, derived_pos)
    print(f"  Vị trí dùng: {pos_used} — Nghi ngờ spoofing: {suspicious}")
    assert suspicious

    print("\n[Task 2] Tất cả kịch bản PASS ✓")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    demo_task1()
    asyncio.run(demo_task2())
    print("\n" + "=" * 60)
    print("ALL DEMO SCENARIOS PASSED")
    print("=" * 60)
