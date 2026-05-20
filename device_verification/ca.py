import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID


class VNULEOCertificateAuthority:
    def __init__(self):
        self.ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        self.ca_cert = self._generate_ca_cert()
        self.revoked_devices: set[str] = set()

    def _generate_ca_cert(self) -> x509.Certificate:
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "VNU-LEO"),
            x509.NameAttribute(NameOID.COMMON_NAME, "VNU-LEO Root CA"),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self.ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )

    def issue_certificate(
        self,
        device_id: str,
        hardware_serial: str,
        public_key_pem: bytes,
    ) -> x509.Certificate:
        """Cấp certificate cho Router mới đăng ký."""
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        public_key = load_pem_public_key(public_key_pem)
        now = datetime.datetime.now(datetime.timezone.utc)
        return (
            x509.CertificateBuilder()
            .subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, device_id),
                x509.NameAttribute(NameOID.SERIAL_NUMBER, hardware_serial),
            ]))
            .issuer_name(self.ca_cert.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(device_id)]),
                critical=False,
            )
            .sign(self.ca_key, hashes.SHA256())
        )

    def verify_certificate(self, cert: x509.Certificate) -> bool:
        """Verify certificate có được CA ký không, chưa hết hạn, chưa bị revoke."""
        try:
            self.ca_cert.public_key().verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                cert.signature_hash_algorithm,
            )
            now = datetime.datetime.now(datetime.timezone.utc)
            if now > cert.not_valid_after_utc:
                return False
            device_id = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
            if device_id in self.revoked_devices:
                return False
            return True
        except Exception:
            return False

    def get_ca_cert_pem(self) -> bytes:
        return self.ca_cert.public_bytes(serialization.Encoding.PEM)

    def revoke(self, device_id: str):
        self.revoked_devices.add(device_id)
