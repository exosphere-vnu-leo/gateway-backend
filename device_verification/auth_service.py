import hashlib
import os
import time

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from cryptography.x509 import load_pem_x509_certificate
from cryptography.x509.oid import NameOID

from .ca import VNULEOCertificateAuthority
from .device_db import DeviceDatabase
from .tpm import SimulatedTPM


class GatewayAuthService:
    NONCE_EXPIRY_SECONDS = 30

    def __init__(self, ca: VNULEOCertificateAuthority, device_db: DeviceDatabase):
        self.ca = ca
        self.device_db = device_db
        self.pending_nonces: dict[str, tuple[bytes, float]] = {}
        self.active_sessions: dict[str, str] = {}   # {session_token: device_id}

    def issue_challenge(self, device_id: str) -> bytes:
        """Bước 1: Gateway phát nonce cho Router."""
        nonce = os.urandom(32)
        self.pending_nonces[device_id] = (nonce, time.time())
        return nonce

    def verify_response(self, device_id: str, cert_pem: bytes, signature: bytes) -> dict:
        """Bước 2: Gateway verify signature từ Router."""

        # 1. Parse và verify certificate
        cert = load_pem_x509_certificate(cert_pem)
        if not self.ca.verify_certificate(cert):
            return {"success": False, "reason": "invalid certificate"}

        # 2. Check device có trong registry không
        if not self.device_db.exists(device_id):
            return {"success": False, "reason": "unknown device"}

        # 3. Lấy và kiểm tra nonce freshness
        if device_id not in self.pending_nonces:
            return {"success": False, "reason": "no pending challenge"}
        nonce, issued_at = self.pending_nonces.pop(device_id)
        if time.time() - issued_at > self.NONCE_EXPIRY_SECONDS:
            return {"success": False, "reason": "nonce expired — replay attack?"}

        # 4. Verify signature — payload phải khớp với RouterAuthClient
        try:
            payload = nonce + device_id.encode()
            cert.public_key().verify(
                signature,
                payload,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        except Exception:
            return {"success": False, "reason": "signature verification failed — spoofing?"}

        # 5. Cấp session token
        session_token = hashlib.sha256(os.urandom(32)).hexdigest()
        self.active_sessions[session_token] = device_id
        return {"success": True, "session_token": session_token}

    def validate_session(self, session_token: str) -> str | None:
        return self.active_sessions.get(session_token)

    def invalidate_session(self, session_token: str):
        self.active_sessions.pop(session_token, None)


class RouterAuthClient:
    def __init__(self, tpm: SimulatedTPM, cert_pem: bytes, device_id: str):
        self.tpm = tpm
        self.cert_pem = cert_pem
        self.device_id = device_id
        self.session_token: str | None = None

    async def authenticate(self, gateway_url: str) -> bool:
        """Router thực hiện toàn bộ flow xác thực."""
        import aiohttp

        async with aiohttp.ClientSession() as session:
            # Bước 1: Lấy nonce
            async with session.post(
                f"{gateway_url}/auth/challenge",
                json={"device_id": self.device_id},
            ) as r:
                nonce = bytes.fromhex((await r.json())["nonce"])

            # Bước 2: Ký bằng TPM — payload khớp GatewayAuthService.verify_response
            payload = nonce + self.device_id.encode()
            signature = self.tpm.sign(payload)

            # Bước 3: Gửi verify
            async with session.post(
                f"{gateway_url}/auth/verify",
                json={
                    "device_id": self.device_id,
                    "certificate": self.cert_pem.hex(),
                    "signature": signature.hex(),
                },
            ) as r:
                result = await r.json()
                if result["success"]:
                    self.session_token = result["session_token"]
                    return True
                return False
