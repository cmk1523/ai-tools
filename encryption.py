"""Encrypt and decrypt text with AES-GCM."""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_GCM_IV_LENGTH = 12
_GCM_TAG_LENGTH = 16


def _as_bytes(value: str | bytes, *, name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        try:
            if stripped and len(stripped) % 2 == 0:
                return bytes.fromhex(stripped)
        except ValueError:
            pass
        return value.encode("utf-8")
    raise TypeError(f"{name} must be str or bytes")


def encrypt(text: str, key: str | bytes, iv: str | bytes) -> str:
    """Encrypt text with AES-GCM.

    ``key`` and ``iv`` may be raw bytes or hexadecimal text. A string that is
    not hexadecimal is encoded as UTF-8. The key must be 16, 24, or 32 bytes.
    A 12-byte IV is the usual GCM nonce length.

    Returns uppercase hex of the IV followed by the ciphertext. The ciphertext
    includes the 16-byte GCM authentication tag.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")

    key_bytes = _as_bytes(key, name="key")
    iv_bytes = _as_bytes(iv, name="iv")
    if len(key_bytes) not in (16, 24, 32):
        raise ValueError("key must be 16, 24, or 32 bytes")
    if not iv_bytes:
        raise ValueError("iv must not be empty")

    ciphertext = AESGCM(key_bytes).encrypt(iv_bytes, text.encode("utf-8"), None)
    return (iv_bytes + ciphertext).hex().upper()


def decrypt(text: str | bytes, key: str | bytes, iv: str | bytes | None = None) -> str:
    """Decrypt AES-GCM text.

    ``text`` is hexadecimal ciphertext, or raw ciphertext bytes. ``key`` may be
    raw bytes or hexadecimal text. A string that is not hexadecimal is encoded
    as UTF-8.

    When ``iv`` is omitted, the first 12 bytes of ``text`` are used as the IV
    and the remainder is the ciphertext, including the authentication tag.
    """
    key_bytes = _as_bytes(key, name="key")
    if len(key_bytes) not in (16, 24, 32):
        raise ValueError("key must be 16, 24, or 32 bytes")

    payload = _as_bytes(text, name="text")
    if iv is None:
        if len(payload) < _GCM_IV_LENGTH + _GCM_TAG_LENGTH:
            raise ValueError("ciphertext is too short to contain an IV and authentication tag")
        iv_bytes = payload[:_GCM_IV_LENGTH]
        ciphertext = payload[_GCM_IV_LENGTH:]
    else:
        iv_bytes = _as_bytes(iv, name="iv")
        if not iv_bytes:
            raise ValueError("iv must not be empty")
        ciphertext = payload

    return AESGCM(key_bytes).decrypt(iv_bytes, ciphertext, None).decode("utf-8")
