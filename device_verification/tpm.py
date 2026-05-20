from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization


class SimulatedTPM:
    """
    Simulate hardware TPM — private key không expose ra ngoài class.
    Trong thực tế: thay bằng calls đến TPM chip qua tpm2-tools.
    """

    def __init__(self):
        self._private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

    def sign(self, data: bytes) -> bytes:
        """Ký data — private key không bao giờ rời TPM."""
        return self._private_key.sign(
            data,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

    def get_public_key(self):
        return self._private_key.public_key()

    def get_public_key_pem(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    # _private_key KHÔNG có getter — không thể đọc ra ngoài
