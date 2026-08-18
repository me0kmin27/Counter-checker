import os
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.getenv("APP_SECRET_KEY")
    if not key:
        raise RuntimeError("APP_SECRET_KEY 환경 변수가 필요합니다.")
    return Fernet(key.encode())


def encrypt_password(password: str) -> bytes:
    return _fernet().encrypt(password.encode())


def decrypt_password(value: bytes) -> str:
    return _fernet().decrypt(value).decode()


def hash_user_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("비밀번호는 10자 이상이어야 합니다.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def verify_user_password(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt, expected = encoded.split("$", 3)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def verify_totp(secret: str, code: str, now: int | None = None) -> bool:
    if not code.isdigit() or len(code) != 6:
        return False
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = (now or int(time.time())) // 30
    for offset in (-1, 0, 1):
        digest = hmac.new(key, struct.pack(">Q", counter + offset), hashlib.sha1).digest()
        index = digest[-1] & 15
        value = (struct.unpack(">I", digest[index:index + 4])[0] & 0x7fffffff) % 1_000_000
        if hmac.compare_digest(f"{value:06d}", code):
            return True
    return False


def totp_uri(secret: str, username: str) -> str:
    return f"otpauth://totp/Counter%20Checker:{quote(username)}?secret={secret}&issuer=Counter%20Checker"
