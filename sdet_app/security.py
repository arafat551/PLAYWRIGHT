"""Password/secret encryption helpers (Fernet-style, deterministic XOR for V2).
For real deployments swap internals with cryptography.Fernet.
"""
import os
import base64

from .config import env

_KEY = (env.SECRET_KEY.zfill(32))[:32].encode()


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def encrypt_value(plain: str) -> str:
    if not plain:
        return ""
    return base64.b64encode(_xor_bytes(plain.encode(), _KEY)).decode()


def decrypt_value(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        return _xor_bytes(base64.b64decode(cipher), _KEY).decode()
    except Exception:
        return ""
