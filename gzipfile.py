"""
title: Compression
description: Compress and decompress a single file with gzip at the highest compression level.
version: 0.1.0
"""

from __future__ import annotations

import gzip
import os
import tempfile
import zlib
from pathlib import Path


# zlib level 9 is the slowest, smallest DEFLATE setting.
_GZIP_LEVEL = zlib.Z_BEST_COMPRESSION
# Memory level 9 gives zlib the most memory for finding matches.
# The gzip module defaults to 8, which can leave a file slightly larger.
_GZIP_MEM_LEVEL = 9
_CHUNK_SIZE = 1024 * 1024


class Tools:
    def __init__(self):
        pass

    def _source_file(self, path: str) -> Path:
        """
        Check that a path points at one existing file.

        :param path: Path supplied by the caller.
        :return: The path as a Path object.
        """
        if not isinstance(path, str):
            raise TypeError("path must be str")
        cleaned = path.strip()
        if not cleaned:
            raise ValueError("Path must not be empty")
        source = Path(cleaned)
        if not source.exists():
            raise FileNotFoundError(f"File not found: {cleaned}")
        if not source.is_file():
            raise ValueError(f"{cleaned} is not a file")
        return source

    def _destination(self, source: Path, suffix_mode: str) -> Path:
        """
        Choose the output path next to the source file.

        :param source: The file being compressed or decompressed.
        :param suffix_mode: ``compress`` adds ``.gz``. ``decompress`` removes it.
        :return: The path to write.
        """
        if suffix_mode == "compress":
            destination = source.with_name(source.name + ".gz")
        elif source.name.lower().endswith(".gz") and len(source.name) > 3:
            destination = source.with_name(source.name[:-3])
        else:
            destination = source.with_name(source.name + ".out")

        if destination.resolve() == source.resolve():
            raise ValueError(f"Refusing to write {source} onto itself")
        return destination

    def _write_atomically(self, destination: Path, write) -> None:
        """
        Write a file beside the destination, then replace the destination.

        A failed compression or decompression leaves the previous file in place.

        :param destination: The file to replace once writing succeeds.
        :param write: Called with a binary file object that receives the bytes.
        :return: None.
        """
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".partial",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as outfile:
                write(outfile)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _compress_into(self, source: Path, outfile) -> None:
        """
        Stream one file into a gzip container at the highest compression level.

        :param source: The file to read.
        :param outfile: Binary file that receives the gzip bytes.
        :return: None.
        """
        compressor = zlib.compressobj(
            _GZIP_LEVEL,
            zlib.DEFLATED,
            16 + zlib.MAX_WBITS,
            _GZIP_MEM_LEVEL,
        )
        with source.open("rb") as infile:
            while True:
                chunk = infile.read(_CHUNK_SIZE)
                if not chunk:
                    break
                compressed = compressor.compress(chunk)
                if compressed:
                    outfile.write(compressed)
            trailer = compressor.flush()
            if trailer:
                outfile.write(trailer)

    def compress_file(self, path: str) -> str:
        """
        Compress one file with gzip at the highest compression level.

        The original file is left in place. The gzip file is written beside it
        with a .gz suffix, and an existing file at that path is overwritten.
        Compression uses zlib level 9 and memory level 9, which is the strongest
        gzip the standard library can produce. The gzip header stores no file name.

        :param path: Path of the file to compress.
        :return: The source and destination paths and their sizes in bytes.
        """
        source = self._source_file(path)
        destination = self._destination(source, "compress")
        source_size = source.stat().st_size
        self._write_atomically(
            destination,
            lambda outfile: self._compress_into(source, outfile),
        )
        destination_size = destination.stat().st_size
        return (
            f"Compressed {source} ({source_size} bytes) "
            f"to {destination} ({destination_size} bytes)."
        )

    def decompress_file(self, path: str) -> str:
        """
        Decompress one gzip file.

        A name ending in .gz is written without that suffix. Any other name is
        written with .out added. An existing file at the destination is overwritten.
        The gzip file is left in place.

        :param path: Path of the gzip file to decompress.
        :return: The source and destination paths and their sizes in bytes.
        """
        source = self._source_file(path)
        destination = self._destination(source, "decompress")
        source_size = source.stat().st_size

        def write(outfile) -> None:
            with gzip.open(source, "rb") as infile:
                while True:
                    chunk = infile.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    outfile.write(chunk)

        try:
            self._write_atomically(destination, write)
        except gzip.BadGzipFile as exc:
            raise ValueError(f"{source} is not a valid gzip file") from exc
        except (EOFError, zlib.error) as exc:
            raise ValueError(f"{source} is not a complete gzip file") from exc

        destination_size = destination.stat().st_size
        return (
            f"Decompressed {source} ({source_size} bytes) "
            f"to {destination} ({destination_size} bytes)."
        )
