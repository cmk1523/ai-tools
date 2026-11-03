"""Create MD5 and SHA-1 hashes of text, bytes, or files."""

from __future__ import annotations

import hashlib
from pathlib import Path


def _as_bytes(data: str | bytes) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    raise TypeError("data must be str or bytes")


def md5_hash(data: str | bytes) -> str:
    """Return the MD5 hex digest of text or bytes."""
    return hashlib.md5(_as_bytes(data)).hexdigest()


def sha1_hash(data: str | bytes) -> str:
    """Return the SHA-1 hex digest of text or bytes."""
    return hashlib.sha1(_as_bytes(data)).hexdigest()