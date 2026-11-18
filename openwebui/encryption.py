"""
title: Encryption
description: Encrypt and decrypt text with a password. Argon2id stretches the password into an AES-256-GCM key. The encrypted output is standard Base64.
requirements: cryptography, argon2-cffi
version: 0.2.0
"""

from __future__ import annotations

import base64
import binascii
import os

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 16 bytes for the Argon2 salt.
SALT_LEN = 16
# 12 bytes recommended for an AES-GCM nonce.
NONCE_LEN = 12
# 32 bytes (256 bits) for AES-256.
KEY_LEN = 32
# Argon2id parameters from the RFC 9106 low-memory profile.
TIME_COST = 3
MEMORY_COST = 65536  # 64 MiB in KiB
PARALLELISM = 4

_GCM_TAG_LENGTH = 16


class Tools:
    def __init__(self):
        pass

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """
        Stretch a password into a 256-bit AES key with Argon2id.

        :param password: Password encoded as UTF-8 and used as the Argon2 secret.
        :param salt: Random salt stored with the ciphertext. Must be 16 bytes.
        :return: 32-byte encryption key.
        """
        if not isinstance(password, str):
            raise TypeError("password must be str")
        if len(salt) != SALT_LEN:
            raise ValueError(f"salt must be {SALT_LEN} bytes")
        return hash_secret_raw(
            secret=password.encode("utf-8"),
            salt=salt,
            time_cost=TIME_COST,
            memory_cost=MEMORY_COST,
            parallelism=PARALLELISM,
            hash_len=KEY_LEN,
            type=Type.ID,
        )

    def encrypt(self, text: str, password: str) -> str:
        """
        Stretch a password with Argon2id and encrypt text with AES-256-GCM.

        A random 16-byte salt and 12-byte nonce are generated for each call.
        The same password therefore produces a different payload every time.
        The result is Base64 of the salt, nonce, and ciphertext, including the
        16-byte authentication tag.

        :param text: The plaintext to encrypt.
        :param password: Password stretched into a 256-bit key with Argon2id.
        :return: Base64 text of the salt, nonce, and ciphertext.
        """
        if not isinstance(text, str):
            raise TypeError("text must be str")

        salt = os.urandom(SALT_LEN)
        nonce = os.urandom(NONCE_LEN)
        derived_key = self._derive_key(password, salt)
        ciphertext = AESGCM(derived_key).encrypt(nonce, text.encode("utf-8"), None)
        # Output is standard Base64 of salt + nonce + ciphertext (including the GCM tag).
        return base64.b64encode(salt + nonce + ciphertext).decode("utf-8")

    def decrypt(self, text: str, password: str) -> str:
        """
        Decrypt text that was encrypted with a password.

        The password is stretched with Argon2id using the salt stored in the payload.
        AES-256-GCM checks the authentication tag before the plaintext is returned.
        A wrong password or a changed payload fails the tag check.

        :param text: Base64 payload of salt, nonce, and ciphertext.
        :param password: Password used to encrypt the text.
        :return: The decrypted plaintext.
        """
        if not isinstance(text, str):
            raise TypeError("text must be str")

        # Input is the standard Base64 string returned by encrypt.
        try:
            payload = base64.b64decode("".join(text.split()), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("text must be Base64") from exc

        minimum = SALT_LEN + NONCE_LEN + _GCM_TAG_LENGTH
        if len(payload) < minimum:
            raise ValueError("ciphertext is too short to contain a salt, nonce, and authentication tag")

        salt = payload[:SALT_LEN]
        nonce = payload[SALT_LEN : SALT_LEN + NONCE_LEN]
        ciphertext = payload[SALT_LEN + NONCE_LEN :]
        derived_key = self._derive_key(password, salt)
        return AESGCM(derived_key).decrypt(nonce, ciphertext, None).decode("utf-8")
