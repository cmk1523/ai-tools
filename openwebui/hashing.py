"""
title: Hashing
description: Create MD5 and SHA-1 hex digests of text.
version: 0.1.0
"""

from __future__ import annotations

import hashlib


class Tools:
    def __init__(self):
        pass

    def _as_bytes(self, data: str | bytes) -> bytes:
        """Turn text or bytes into bytes."""
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode("utf-8")
        raise TypeError("data must be str or bytes")

    def md5_hash(self, data: str) -> str:
        """
        Hash text with MD5 and return the hex digest.

        :param data: Text string to hash.
        :return: The MD5 hex digest string.
        """
        return hashlib.md5(self._as_bytes(data)).hexdigest()

    def sha1_hash(self, data: str) -> str:
        """
        Hash text with SHA-1 and return the hex digest.

        :param data: Text string to hash.
        :return: The SHA-1 hex digest string.
        """
        return hashlib.sha1(self._as_bytes(data)).hexdigest()