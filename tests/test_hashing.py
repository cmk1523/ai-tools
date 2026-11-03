"""Tests for MD5 and SHA-1 helpers."""

import hashlib
from pathlib import Path

import pytest

from hashing import md5_file, md5_hash, sha1_file, sha1_hash


def test_md5_and_sha1_of_known_text():
    assert md5_hash("") == "d41d8cd98f00b204e9800998ecf8427e"
    assert sha1_hash("") == "da39a3ee5e6b4b0d3255bfef95601890afd80709"
    assert md5_hash("hello") == "5d41402abc4b2a76b9719d911017c592"
    assert sha1_hash("hello") == "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"


def test_string_and_utf8_bytes_match():
    text = "café"
    encoded = text.encode("utf-8")
    assert md5_hash(text) == md5_hash(encoded) == hashlib.md5(encoded).hexdigest()
    assert sha1_hash(text) == sha1_hash(encoded) == hashlib.sha1(encoded).hexdigest()


def test_hash_rejects_non_text_data():
    with pytest.raises(TypeError, match="str or bytes"):
        md5_hash(123)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="str or bytes"):
        sha1_hash(None)  # type: ignore[arg-type]


def test_file_hashes_match_contents(tmp_path: Path):
    payload = b"\x00\x01hello\xff" * 100
    file_path = tmp_path / "sample.bin"
    file_path.write_bytes(payload)

    assert md5_file(file_path) == hashlib.md5(payload).hexdigest()
    assert sha1_file(str(file_path)) == hashlib.sha1(payload).hexdigest()
    assert md5_file(file_path, chunk_size=1) == md5_hash(payload)
    assert sha1_file(file_path, chunk_size=7) == sha1_hash(payload)


def test_empty_file_hash(tmp_path: Path):
    file_path = tmp_path / "empty.bin"
    file_path.write_bytes(b"")
    assert md5_file(file_path) == md5_hash(b"")
    assert sha1_file(file_path) == sha1_hash(b"")


def test_missing_file_raises(tmp_path: Path):
    missing = tmp_path / "missing.bin"
    with pytest.raises(FileNotFoundError):
        md5_file(missing)
    with pytest.raises(FileNotFoundError):
        sha1_file(missing)
