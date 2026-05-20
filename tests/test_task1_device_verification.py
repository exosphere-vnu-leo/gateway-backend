"""
Tests for Task 1 — Device Verification & Anti-Spoofing.

Các trường hợp:
  - Happy path: router hợp lệ xác thực thành công
  - Fake TPM: router dùng key khác → signature sai
  - Replay attack: nonce hết hạn
  - Revoked device: certificate bị thu hồi
  - Unknown device: chưa đăng ký trong DB
  - No challenge: verify không có nonce trước
  - Session token: cấp đúng và validate được
"""
import time
import pytest
from cryptography.hazmat.primitives import serialization

from device_verification import (
    SimulatedTPM,
    VNULEOCertificateAuthority,
    DeviceDatabase,
    GatewayAuthService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ca():
    return VNULEOCertificateAuthority()


@pytest.fixture
def db():
    return DeviceDatabase()


@pytest.fixture
def auth(ca, db):
    return GatewayAuthService(ca, db)


@pytest.fixture
def registered_device(ca, db, auth):
    """Router hợp lệ đã đăng ký: trả về (device_id, tpm, cert_pem)."""
    device_id = "RTR-TEST-001"
    tpm = SimulatedTPM()
    cert = ca.issue_certificate(device_id, "SN-XXXX-001", tpm.get_public_key_pem())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    db.add(device_id, "SN-XXXX-001")
    return device_id, tpm, cert_pem


# ---------------------------------------------------------------------------
# Task 1 — happy path
# ---------------------------------------------------------------------------

def test_valid_device_authenticates(auth, registered_device):
    device_id, tpm, cert_pem = registered_device

    nonce = auth.issue_challenge(device_id)
    sig   = tpm.sign(nonce + device_id.encode())
    result = auth.verify_response(device_id, cert_pem, sig)

    assert result["success"] is True
    assert "session_token" in result


def test_session_token_is_valid_after_auth(auth, registered_device):
    device_id, tpm, cert_pem = registered_device

    nonce  = auth.issue_challenge(device_id)
    sig    = tpm.sign(nonce + device_id.encode())
    result = auth.verify_response(device_id, cert_pem, sig)

    token = result["session_token"]
    assert auth.validate_session(token) == device_id


def test_session_invalidated_after_logout(auth, registered_device):
    device_id, tpm, cert_pem = registered_device

    nonce  = auth.issue_challenge(device_id)
    sig    = tpm.sign(nonce + device_id.encode())
    result = auth.verify_response(device_id, cert_pem, sig)

    token = result["session_token"]
    auth.invalidate_session(token)
    assert auth.validate_session(token) is None


# ---------------------------------------------------------------------------
# Task 1 — anti-spoofing
# ---------------------------------------------------------------------------

def test_fake_tpm_rejected(auth, registered_device):
    """Router dùng key khác (fake TPM) → signature không khớp với cert."""
    device_id, _, cert_pem = registered_device
    fake_tpm = SimulatedTPM()

    nonce  = auth.issue_challenge(device_id)
    sig    = fake_tpm.sign(nonce + device_id.encode())
    result = auth.verify_response(device_id, cert_pem, sig)

    assert result["success"] is False
    assert "spoofing" in result["reason"]


def test_replay_attack_rejected(auth, registered_device):
    """Nonce cũ bị giả lập quá hạn 30s."""
    device_id, tpm, cert_pem = registered_device

    nonce = auth.issue_challenge(device_id)
    sig   = tpm.sign(nonce + device_id.encode())
    # Override timestamp về quá khứ
    auth.pending_nonces[device_id] = (nonce, time.time() - 31)

    result = auth.verify_response(device_id, cert_pem, sig)
    assert result["success"] is False
    assert "replay" in result["reason"].lower() or "expired" in result["reason"].lower()


def test_no_pending_challenge_rejected(auth, registered_device):
    """verify_response khi chưa issue_challenge → không có nonce."""
    device_id, tpm, cert_pem = registered_device
    fake_sig = tpm.sign(b"no-nonce" + device_id.encode())

    result = auth.verify_response(device_id, cert_pem, fake_sig)
    assert result["success"] is False
    assert "challenge" in result["reason"].lower()


def test_revoked_device_rejected(auth, ca, registered_device):
    """Certificate bị thu hồi → verify trả về False."""
    device_id, tpm, cert_pem = registered_device
    ca.revoke(device_id)

    nonce  = auth.issue_challenge(device_id)
    sig    = tpm.sign(nonce + device_id.encode())
    result = auth.verify_response(device_id, cert_pem, sig)

    assert result["success"] is False
    assert "certificate" in result["reason"].lower()


def test_unknown_device_rejected(auth, ca):
    """Device chưa được add vào DB."""
    tpm = SimulatedTPM()
    cert = ca.issue_certificate("RTR-UNKNOWN", "SN-GHOST", tpm.get_public_key_pem())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    nonce  = auth.issue_challenge("RTR-UNKNOWN")
    sig    = tpm.sign(nonce + b"RTR-UNKNOWN")
    result = auth.verify_response("RTR-UNKNOWN", cert_pem, sig)

    assert result["success"] is False
    assert "unknown" in result["reason"].lower()


def test_nonce_consumed_after_use(auth, registered_device):
    """Nonce bị xóa sau khi dùng → không thể dùng lại."""
    device_id, tpm, cert_pem = registered_device

    nonce = auth.issue_challenge(device_id)
    sig   = tpm.sign(nonce + device_id.encode())
    auth.verify_response(device_id, cert_pem, sig)   # dùng lần 1

    # Thử verify lần 2 với cùng sig — nonce đã bị pop
    result2 = auth.verify_response(device_id, cert_pem, sig)
    assert result2["success"] is False


def test_two_devices_independent(auth, ca, db):
    """Hai thiết bị không ảnh hưởng nhau."""
    results = []
    for i in range(2):
        did  = f"RTR-MULTI-{i:03d}"
        tpm  = SimulatedTPM()
        cert = ca.issue_certificate(did, f"SN-{i:04d}", tpm.get_public_key_pem())
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        db.add(did, f"SN-{i:04d}")

        nonce  = auth.issue_challenge(did)
        sig    = tpm.sign(nonce + did.encode())
        results.append(auth.verify_response(did, cert_pem, sig))

    assert all(r["success"] for r in results)
    assert results[0]["session_token"] != results[1]["session_token"]
