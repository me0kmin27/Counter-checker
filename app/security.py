import os

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
