"""
title: Nextcloud gzip tests
description: Tests for gzip compression and decompression of one Nextcloud file.
requirements: pytest, requests
version: 0.2.0
"""

import gzip
from urllib.parse import unquote, urlparse

from nextcloud import Tools


def remote_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    marker = "/remote.php/dav/files/"
    rest = path[path.find(marker) + len(marker) :]
    _user, _slash, remote = rest.partition("/")
    return remote.strip("/")


def propfind_xml(path: str, folder: bool) -> bytes:
    collection = "<d:collection />" if folder else ""
    href = "/remote.php/dav/files/admin/" + path
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>{href}</d:href>
    <d:propstat>
      <d:status>HTTP/1.1 200 OK</d:status>
      <d:prop>
        <d:resourcetype>{collection}</d:resourcetype>
      </d:prop>
    </d:propstat>
  </d:response>
</d:multistatus>
"""
    return xml.encode("utf-8")


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content

    def iter_content(self, chunk_size: int = 1):
        view = self.content
        for start in range(0, len(view), chunk_size):
            yield view[start : start + chunk_size]

    def close(self) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeNextcloud:
    def __init__(self, files: dict[str, bytes] | None = None, folders: set[str] | None = None):
        self.files = dict(files or {})
        self.folders = set(folders or set())
        self.puts: list[str] = []
        self.status_override: int | None = None

    def _is_folder(self, path: str) -> bool:
        if not path:
            return True
        if path in self.folders:
            return True
        prefix = path + "/"
        return any(name.startswith(prefix) for name in self.files)

    def request(self, method: str, url: str, **_kwargs) -> FakeResponse:
        if self.status_override is not None:
            return FakeResponse(self.status_override)
        path = remote_from_url(url)
        if method == "PROPFIND":
            if path in self.files:
                return FakeResponse(207, propfind_xml(path, folder=False))
            if self._is_folder(path):
                return FakeResponse(207, propfind_xml(path, folder=True))
            return FakeResponse(404)
        if method == "MKCOL":
            if path in self.files:
                return FakeResponse(405)
            existed = self._is_folder(path)
            self.folders.add(path)
            return FakeResponse(405 if existed else 201)
        return FakeResponse(500)

    def get(self, url: str, timeout: int | None = None, stream: bool = False) -> FakeResponse:
        del timeout, stream
        if self.status_override is not None:
            return FakeResponse(self.status_override)
        path = remote_from_url(url)
        if path not in self.files:
            return FakeResponse(404)
        return FakeResponse(200, self.files[path])

    def put(self, url: str, data=None, timeout: int | None = None) -> FakeResponse:
        del timeout
        if self.status_override is not None:
            return FakeResponse(self.status_override)
        path = remote_from_url(url)
        if hasattr(data, "read"):
            data = data.read()
        existed = path in self.files
        self.puts.append(path)
        self.files[path] = data
        return FakeResponse(204 if existed else 201)


def tool_with(files: dict[str, bytes] | None = None, folders: set[str] | None = None):
    tools = Tools()
    fake = FakeNextcloud(files, folders)
    tools._session = lambda: fake
    return tools, fake


def test_compress_generates_gz_path_and_keeps_original():
    payload = b"hello nextcloud " * 100
    tools, fake = tool_with({"Notes/report.txt": payload})

    message = tools.compress_file("Notes/report.txt")
    stored = fake.files["Notes/report.txt.gz"]

    assert message == (
        f"Compressed Notes/report.txt ({len(payload)} bytes) "
        f"to Notes/report.txt.gz ({len(stored)} bytes) in Nextcloud."
    )
    assert gzip.decompress(stored) == payload
    assert fake.files["Notes/report.txt"] == payload
    assert "Notes" in fake.folders


def test_compress_uses_output_path_and_overwrites():
    tools, fake = tool_with(
        {
            "Notes/report.txt": b"abcabcabc",
            "Archive/report.gz": b"old",
        }
    )

    message = tools.compress_file("Notes/report.txt", "Archive/report.gz")
    stored = fake.files["Archive/report.gz"]

    assert message == (
        f"Compressed Notes/report.txt (9 bytes) "
        f"to Archive/report.gz ({len(stored)} bytes) in Nextcloud. "
        "The existing file was overwritten."
    )
    assert gzip.decompress(stored) == b"abcabcabc"
    assert "Archive" in fake.folders


def test_empty_output_path_is_treated_as_omitted():
    tools, fake = tool_with({"plain.txt": b"zzzz" * 20})
    tools.compress_file("/plain.txt", "   ")
    assert "plain.txt.gz" in fake.files
    assert gzip.decompress(fake.files["plain.txt.gz"]) == b"zzzz" * 20


def test_decompress_roundtrip_and_custom_path():
    payload = bytes(range(256)) * 10
    tools, fake = tool_with({"Notes/report.txt": payload})
    tools.compress_file("Notes/report.txt")

    generated = tools.decompress_file("Notes/report.txt.gz", "Restored/report.txt")
    assert fake.files["Restored/report.txt"] == payload
    assert "to Restored/report.txt" in generated

    fake.files.pop("Notes/report.txt")
    tools.decompress_file("Notes/report.txt.gz")
    assert fake.files["Notes/report.txt"] == payload


def test_decompress_strips_uppercase_gz_or_adds_out():
    tools, fake = tool_with(
        {
            "bundle.TAR.GZ": gzip.compress(b"archive-body"),
            "payload.bin": gzip.compress(b"abc"),
            ".gz": gzip.compress(b"kept"),
        }
    )

    tools.decompress_file("bundle.TAR.GZ")
    tools.decompress_file("payload.bin")
    tools.decompress_file(".gz")

    assert fake.files["bundle.TAR"] == b"archive-body"
    assert fake.files["payload.bin.out"] == b"abc"
    assert fake.files[".gz.out"] == b"kept"
    assert gzip.decompress(fake.files[".gz"]) == b"kept"


def test_rejects_same_path_folder_missing_and_bad_gzip():
    tools, fake = tool_with(
        {
            "Notes/report.txt": b"text",
            "Notes/bad.gz": b"this is not gzip",
        },
        folders={"Notes"},
    )

    assert tools.compress_file("Notes/report.txt", "Notes/report.txt").startswith(
        "Compress failed: output path is the same"
    )
    assert tools.compress_file("Notes").startswith("Compress failed: Notes is a folder")
    assert tools.compress_file("missing.bin").startswith("Compress failed: missing.bin was not found")
    assert tools.compress_file("Notes/../secret.txt").startswith("Compress failed:")
    assert tools.decompress_file("   ").startswith("Decompress failed:")
    assert (
        tools.decompress_file("Notes/bad.gz")
        == "Decompress failed: Notes/bad.gz is not a valid gzip file."
    )
    assert "Notes/bad" not in fake.files
    assert fake.puts == []


def test_truncated_gzip_is_not_uploaded():
    tools, fake = tool_with({"cut.gz": gzip.compress(b"hello world" * 100)[:12]})
    message = tools.decompress_file("cut.gz")
    assert message == "Decompress failed: cut.gz is not a complete gzip file."
    assert "cut" not in fake.files
    assert fake.puts == []


def test_authentication_failure_is_reported():
    tools, fake = tool_with({"Notes/report.txt": b"text"})
    fake.status_override = 401
    assert "authentication failed" in tools.compress_file("Notes/report.txt")
