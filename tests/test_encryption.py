"""Tests for AES-GCM helpers in encryption.py."""

import pytest
from cryptography.exceptions import InvalidTag

from encryption import decrypt, encrypt

KEY_128 = "00112233445566778899aabbccddeeff"
KEY_192 = "00112233445566778899aabbccddeeff0011223344556677"
KEY_256 = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
IV = "0102030405060708090a0b0c"


def test_encrypt_empty_plaintext_matches_nist_vector():
    # NIST SP 800-38D, AES-GCM test case 1: empty plaintext, zero key and nonce.
    key = "00" * 16
    iv = "00" * 12
    assert encrypt("", key, iv) == "00000000000000000000000058E2FCCEFA7E3061367F1D57A4E7455A"


def test_roundtrip_with_hex_key_and_iv():
    token = encrypt("hello", KEY_128, IV)
    assert token == token.upper()
    assert decrypt(token, KEY_128) == "hello"
    assert decrypt(token.lower(), KEY_128.upper()) == "hello"


def test_roundtrip_with_raw_bytes_and_explicit_iv():
    key = bytes.fromhex(KEY_256)
    iv = bytes.fromhex(IV)
    token = encrypt("secret", key, iv)
    payload = bytes.fromhex(token)
    ciphertext = payload[len(iv) :]
    assert decrypt(ciphertext, key, iv) == "secret"
    assert decrypt(ciphertext.hex(), key.hex(), iv.hex()) == "secret"


def test_roundtrip_aes_192_and_utf8_key():
    key = "not-hex-key-material!!!!"  # 24 UTF-8 bytes, not valid hexadecimal
    iv = b"nonce-12byte"
    assert len(key.encode("utf-8")) == 24
    assert decrypt(encrypt("café 🔐", key, iv), key, None) == "café 🔐"


def test_key_lengths():
    plaintext = "length check"
    iv = IV
    assert decrypt(encrypt(plaintext, KEY_128, iv), KEY_128) == plaintext
    assert decrypt(encrypt(plaintext, KEY_192, iv), KEY_192) == plaintext
    assert decrypt(encrypt(plaintext, KEY_256, iv), KEY_256) == plaintext


def test_nonstandard_nonce_requires_explicit_iv():
    key = bytes.fromhex(KEY_128)
    iv = b"0123456789abcdef"
    token = encrypt("payload", key, iv)
    ciphertext = bytes.fromhex(token)[len(iv) :]
    assert decrypt(ciphertext, key, iv) == "payload"
    with pytest.raises(InvalidTag):
        decrypt(token, key)


def test_whitespace_around_hex_key_is_ignored():
    padded = f"  {KEY_128.upper()}  "
    assert decrypt(encrypt("trim", padded, IV), KEY_128) == "trim"


def test_encrypt_rejects_non_string_text():
    with pytest.raises(TypeError, match="text must be str"):
        encrypt(b"bytes", KEY_128, IV)  # type: ignore[arg-type]


def test_rejects_invalid_key_length_and_type():
    with pytest.raises(ValueError, match="16, 24, or 32"):
        encrypt("x", "abcd", IV)
    with pytest.raises(ValueError, match="16, 24, or 32"):
        decrypt("00" * 40, b"\x00" * 15, IV)
    with pytest.raises(TypeError, match="key must be str or bytes"):
        encrypt("x", 123, IV)  # type: ignore[arg-type]


def test_rejects_empty_iv():
    with pytest.raises(ValueError, match="iv must not be empty"):
        encrypt("x", KEY_128, "")
    with pytest.raises(ValueError, match="iv must not be empty"):
        decrypt("00" * 32, KEY_128, b"")


def test_decrypt_rejects_payload_too_short_for_embedded_iv():
    with pytest.raises(ValueError, match="too short"):
        decrypt("aabbcc", KEY_128)


def test_decrypt_rejects_tampered_ciphertext_and_wrong_key():
    token = encrypt("authentic", KEY_128, IV)
    flipped = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(InvalidTag):
        decrypt(flipped, KEY_128)
    with pytest.raises(InvalidTag):
        decrypt(token, KEY_256)


def test_embedded_iv_is_not_ciphertext_when_iv_is_passed_again():
    token = encrypt("once", KEY_128, IV)
    with pytest.raises(InvalidTag):
        decrypt(token, KEY_128, IV)
