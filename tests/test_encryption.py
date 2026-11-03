"""
title: Encryption tests
description: Tests for password encryption with Argon2id and AES-256-GCM.
requirements: pytest, cryptography, argon2-cffi
version: 0.2.0
"""

import base64

import pytest
from cryptography.exceptions import InvalidTag

from encryption import KEY_LEN, NONCE_LEN, SALT_LEN, Tools

tools = Tools()
decrypt = tools.decrypt
encrypt = tools.encrypt

PASSWORD = "correct horse battery staple"


def _payload(token: str) -> bytes:
    return base64.b64decode(token)


def test_roundtrip_password_and_utf8_text():
    token = encrypt("café 🔐", PASSWORD)
    assert decrypt(token, PASSWORD) == "café 🔐"


def test_empty_plaintext_roundtrips():
    assert decrypt(encrypt("", PASSWORD), PASSWORD) == ""


def test_each_encryption_uses_a_fresh_salt_and_nonce():
    first = _payload(encrypt("same", PASSWORD))
    second = _payload(encrypt("same", PASSWORD))
    assert first != second
    assert first[:SALT_LEN] != second[:SALT_LEN]
    assert first[SALT_LEN : SALT_LEN + NONCE_LEN] != second[SALT_LEN : SALT_LEN + NONCE_LEN]


def test_payload_is_salt_nonce_and_tagged_ciphertext():
    token = encrypt("layout", PASSWORD)
    payload = _payload(token)
    assert token == base64.b64encode(payload).decode("utf-8")
    assert len(payload) >= SALT_LEN + NONCE_LEN + 16
    # AES-GCM appends a 16-byte tag to the plaintext bytes.
    assert len(payload) - SALT_LEN - NONCE_LEN == len("layout".encode("utf-8")) + 16


def test_whitespace_in_base64_payload_is_ignored():
    token = encrypt("trim", PASSWORD)
    wrapped = "\n".join(token[i : i + 8] for i in range(0, len(token), 8))
    assert decrypt(f"  {wrapped}  ", PASSWORD) == "trim"


def test_wrong_password_and_tampered_payload_fail_authentication():
    token = encrypt("authentic", PASSWORD)
    with pytest.raises(InvalidTag):
        decrypt(token, "wrong password")

    payload = bytearray(_payload(token))
    payload[-1] ^= 0x01
    flipped = base64.b64encode(payload).decode("utf-8")
    with pytest.raises(InvalidTag):
        decrypt(flipped, PASSWORD)


def test_rejects_non_string_text_and_password():
    with pytest.raises(TypeError, match="text must be str"):
        encrypt(b"bytes", PASSWORD)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="password must be str"):
        encrypt("x", b"password")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="text must be str"):
        decrypt(b"abc", PASSWORD)  # type: ignore[arg-type]


def test_rejects_invalid_base64_and_short_payload():
    with pytest.raises(ValueError, match="Base64"):
        decrypt("not base64 !!!", PASSWORD)
    short = base64.b64encode(b"\x00" * (SALT_LEN + NONCE_LEN + 15)).decode("utf-8")
    with pytest.raises(ValueError, match="too short"):
        decrypt(short, PASSWORD)


def test_derived_key_is_32_bytes():
    salt = b"\x01" * SALT_LEN
    assert len(tools._derive_key(PASSWORD, salt)) == KEY_LEN
    assert tools._derive_key(PASSWORD, salt) == tools._derive_key(PASSWORD, salt)
