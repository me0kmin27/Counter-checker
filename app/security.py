import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _fernet() -> Fernet:
    key = os.getenv("APP_SECRET_KEY")
    if not key:
        raise RuntimeError("APP_SECRET_KEY 환경 변수가 필요합니다.")
    return Fernet(key.encode())


def encrypt_password(password: str) -> bytes:
    return _fernet().encrypt(password.encode())


def decrypt_password(value: bytes) -> str:
    try:
        return _fernet().decrypt(value).decode()
    except InvalidToken:
        # The original PHP service stored IV + GCM tag + ciphertext as base64.
        # Supporting it here lets existing production accounts survive the
        # PHP-to-Python deployment; the next password update writes Fernet.
        try:
            payload = base64.b64decode(value, validate=True)
            key = base64.urlsafe_b64decode(os.environ["APP_SECRET_KEY"])
            if len(payload) < 29 or len(key) != 32:
                raise ValueError
            iv, tag, ciphertext = payload[:12], payload[12:28], payload[28:]
            return AESGCM(key).decrypt(iv, ciphertext + tag, None).decode()
        except (binascii.Error, InvalidTag, KeyError, ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("저장된 POP 비밀번호를 복호화할 수 없습니다.") from exc
