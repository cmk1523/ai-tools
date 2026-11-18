"""
title: Compression tests
description: Tests for gzip compression and decompression of a single file.
requirements: pytest
version: 0.1.0
"""

import gzip
import random
import zlib
from pathlib import Path

import pytest

from gzipfile import Tools


tools = Tools()
compress_file = tools.compress_file
decompress_file = tools.decompress_file


def test_roundtrip_binary_file(tmp_path: Path):
    payload = bytes(range(256)) * 50
    source = tmp_path / "sample.bin"
    source.write_bytes(payload)

    compressed = tmp_path / "sample.bin.gz"
    message = compress_file(str(source))
    gzip_bytes = compressed.read_bytes()

    assert message == (
        f"Compressed {source} ({len(payload)} bytes) "
        f"to {compressed} ({len(gzip_bytes)} bytes)."
    )
    assert gzip_bytes[:2] == b"\x1f\x8b"
    assert gzip.decompress(gzip_bytes) == payload
    assert source.read_bytes() == payload

    restored = tmp_path / "restored.bin"
    source.rename(restored)
    assert decompress_file(str(compressed)) == (
        f"Decompressed {compressed} ({len(gzip_bytes)} bytes) "
        f"to {source} ({len(payload)} bytes)."
    )
    assert source.read_bytes() == payload


def test_empty_file_roundtrip(tmp_path: Path):
    source = tmp_path / "empty.txt"
    source.write_bytes(b"")
    compress_file(str(source))
    compressed = tmp_path / "empty.txt.gz"
    assert gzip.decompress(compressed.read_bytes()) == b""
    source.unlink()
    decompress_file(str(compressed))
    assert source.read_bytes() == b""


def test_compression_is_at_least_as_small_as_gzip_level_9(tmp_path: Path):
    rng = random.Random(0)
    payload = bytes(rng.getrandbits(8) for _ in range(20000))
    source = tmp_path / "random.bin"
    source.write_bytes(payload)

    compress_file(str(source))
    produced = (tmp_path / "random.bin.gz").read_bytes()
    stdlib = gzip.compress(payload, compresslevel=zlib.Z_BEST_COMPRESSION)

    assert len(produced) <= len(stdlib)
    assert gzip.decompress(produced) == payload


def test_repeated_compression_is_identical(tmp_path: Path):
    source = tmp_path / "notes.txt"
    source.write_text("the same text " * 1000, encoding="utf-8")
    compress_file(str(source))
    first = (tmp_path / "notes.txt.gz").read_bytes()
    compress_file(str(source))
    assert (tmp_path / "notes.txt.gz").read_bytes() == first


def test_uppercase_gz_suffix_is_removed(tmp_path: Path):
    payload = b"archive-body"
    archive = tmp_path / "bundle.TAR.GZ"
    archive.write_bytes(gzip.compress(payload))
    decompress_file(str(archive))
    restored = tmp_path / "bundle.TAR"
    assert restored.read_bytes() == payload
    assert archive.exists()


def test_name_without_gz_suffix_writes_out_file(tmp_path: Path):
    source = tmp_path / "payload.bin"
    source.write_bytes(gzip.compress(b"abc"))
    decompress_file(str(source))
    assert (tmp_path / "payload.bin.out").read_bytes() == b"abc"


def test_bare_gz_name_does_not_erase_itself(tmp_path: Path):
    source = tmp_path / ".gz"
    source.write_bytes(gzip.compress(b"kept"))
    decompress_file(str(source))
    assert (tmp_path / ".gz.out").read_bytes() == b"kept"
    assert gzip.decompress(source.read_bytes()) == b"kept"


def test_missing_path_directory_and_empty_path(tmp_path: Path):
    missing = tmp_path / "missing.bin"
    with pytest.raises(FileNotFoundError, match="File not found"):
        compress_file(str(missing))

    with pytest.raises(ValueError, match="not a file"):
        compress_file(str(tmp_path))

    with pytest.raises(ValueError, match="must not be empty"):
        decompress_file("   ")


def test_rejects_non_text_path():
    with pytest.raises(TypeError, match="path must be str"):
        compress_file(123)  # type: ignore[arg-type]


def test_rejects_invalid_and_truncated_gzip(tmp_path: Path):
    invalid = tmp_path / "notes.gz"
    invalid.write_bytes(b"this is not gzip")
    with pytest.raises(ValueError, match="not a valid gzip file"):
        decompress_file(str(invalid))
    assert not (tmp_path / "notes").exists()

    truncated = tmp_path / "cut.gz"
    truncated.write_bytes(gzip.compress(b"hello world" * 100)[:12])
    with pytest.raises(ValueError, match="not a complete gzip file"):
        decompress_file(str(truncated))
    assert not (tmp_path / "cut").exists()
