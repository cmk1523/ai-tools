"""
title: Hashing tests
description: Tests for MD5 and SHA-1 hashes of text and bytes.
requirements: pytest
version: 0.1.0
"""

import hashlib

import pytest

from hashing import Tools


tools = Tools()
md5_hash = tools.md5_hash
sha1_hash = tools.sha1_hash


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
