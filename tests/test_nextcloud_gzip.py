"""
title: Nextcloud gzip tests
description: Tests for gzip compression and decompression of one Nextcloud file, CNN Lite headline extraction, and downloading any URL into Nextcloud.
requirements: pytest, requests
version: 0.4.0
"""

import gzip
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

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


_SAMPLE = Path(__file__).resolve().parent / "download-2026-11-05.htm"
_ONE_STORY = """
<header class="header--lite"><span>11/5/2026</span></header>
<li class="card--lite"><a href="/politics/story">One story</a></li>
""".encode("utf-8")


def test_extract_cnn_headlines_writes_csv_beside_the_html_file():
    html = _SAMPLE.read_bytes()
    tools, fake = tool_with({"Inbox/download-2026-11-05.htm": html})

    message = tools.extract_cnn_headlines("/Inbox/download-2026-11-05.htm", "   ")
    stored = fake.files["Inbox/download-2026-11-05.csv"].decode("utf-8")

    assert message == (
        "Extracted 100 headlines from Inbox/download-2026-11-05.htm "
        "to Inbox/download-2026-11-05.csv in Nextcloud."
    )
    assert stored.startswith("headline,url,date\n")
    assert stored.count("\n") == 101
    assert "Tracking the House: See which races are still counting votes" in stored
    assert fake.files["Inbox/download-2026-11-05.htm"] == html
    assert "Inbox" in fake.folders


def test_extract_cnn_headlines_uses_output_path_and_overwrites():
    tools, fake = tool_with(
        {
            "Inbox/lite.HTML": _ONE_STORY,
            "Out/headlines.csv": b"old",
        }
    )

    message = tools.extract_cnn_headlines("Inbox/lite.HTML", "Out/headlines.csv")
    stored = fake.files["Out/headlines.csv"].decode("utf-8")

    assert message == (
        "Extracted 1 headline from Inbox/lite.HTML "
        "to Out/headlines.csv in Nextcloud. The existing file was overwritten."
    )
    assert stored == (
        "headline,url,date\n"
        "One story,https://www.cnn.com/politics/story,2026-11-05\n"
    )
    assert fake.files["Inbox/lite.HTML"] == _ONE_STORY
    assert "Out" in fake.folders


def test_extract_cnn_headlines_rejects_bad_input_without_uploading():
    tools, fake = tool_with(
        {
            "Notes/page.txt": b"<html></html>",
            "Notes/page.htm": b"\xff\xfe",
            "Notes/undated.html": (
                b'<li class="card--lite"><a href="/politics/story">No date here</a></li>'
            ),
        },
        folders={"Notes", "Archive/saved.html"},
    )

    assert tools.extract_cnn_headlines("Notes/page.txt").startswith(
        "Extract failed: Notes/page.txt must be a .htm or .html file."
    )
    assert tools.extract_cnn_headlines("Archive/saved.html").startswith(
        "Extract failed: Archive/saved.html is a folder"
    )
    assert tools.extract_cnn_headlines("Notes/undated.html", "Notes").startswith(
        "Extract failed: Notes is a folder"
    )
    assert tools.extract_cnn_headlines("missing.htm").startswith(
        "Extract failed: missing.htm was not found."
    )
    assert tools.extract_cnn_headlines("Notes/../secret.htm").startswith(
        "Extract failed:"
    )
    assert tools.extract_cnn_headlines("   ").startswith("Extract failed:")
    assert tools.extract_cnn_headlines("Notes/page.htm", "Notes/page.htm").startswith(
        "Extract failed: output path is the same"
    )
    assert (
        tools.extract_cnn_headlines("Notes/page.htm")
        == "Extract failed: Notes/page.htm is not UTF-8."
    )
    assert "No date near the top" in tools.extract_cnn_headlines("Notes/undated.html")
    assert fake.puts == []


def test_extract_cnn_headlines_reports_authentication_failure():
    tools, fake = tool_with({"page.htm": _ONE_STORY})
    fake.status_override = 401
    assert "authentication failed" in tools.extract_cnn_headlines("page.htm")
    assert "page.csv" not in fake.files


class WebResponse:
    def __init__(self, content: bytes, content_type: str, url: str, status_code: int = 200):
        self.content = content
        self.headers = {"content-type": content_type}
        self.url = url
        self.status_code = status_code

    def iter_content(self, chunk_size: int = 1):
        view = self.content
        for start in range(0, len(view), chunk_size):
            yield view[start : start + chunk_size]

    def close(self) -> None:
        return None


def patch_fetch(monkeypatch, response: WebResponse | None = None, error: Exception | None = None):
    def fake_get(url, headers=None, timeout=None, stream=None, allow_redirects=None):
        del url, headers, timeout, stream, allow_redirects
        if error is not None:
            raise error
        return response

    monkeypatch.setattr("nextcloud.requests.get", fake_get)


def test_webpage_downloader_strips_html_and_names_the_file_from_the_url(monkeypatch):
    html = """
    <html><head>
      <style>body { color: red; }</style>
      <link rel="stylesheet" href="/app.css">
      <script>alert(1)</script>
    </head>
    <body style="margin:0" onclick="track()">
      <!-- tracker -->
      <p>Hello page</p>
    </body></html>
    """.encode("utf-8")
    patch_fetch(
        monkeypatch,
        WebResponse(html, "text/html; charset=utf-8", "https://example.com/news/story.html"),
    )
    tools, fake = tool_with()

    message = tools.webpage_downloader("  https://example.com/news/story.html  ")
    name = f"download-{date.today().isoformat()}.html"
    stored = fake.files[name].decode("utf-8")

    assert message == (
        "Downloaded https://example.com/news/story.html "
        f"({len(fake.files[name])} bytes) to {name} in Nextcloud."
    )
    assert "Hello page" in stored
    assert "<script" not in stored
    assert "<style" not in stored
    assert "stylesheet" not in stored
    assert "onclick" not in stored
    assert "tracker" not in stored
    assert "margin" not in stored


def test_webpage_downloader_stores_any_url_bytes_and_uses_output_path(monkeypatch):
    payload = b"%PDF-1.4\n%\x00\x01 binary"
    patch_fetch(
        monkeypatch,
        WebResponse(payload, "application/pdf", "https://cdn.example/files/report.PDF?download=1"),
    )
    tools, fake = tool_with({"Inbox/saved.pdf": b"old"})

    message = tools.webpage_downloader(
        "https://cdn.example/files/report.PDF?download=1",
        " /Inbox/saved.pdf ",
    )

    assert message == (
        "Downloaded https://cdn.example/files/report.PDF?download=1 "
        f"({len(payload)} bytes) to Inbox/saved.pdf in Nextcloud. "
        "The existing file was overwritten."
    )
    assert fake.files["Inbox/saved.pdf"] == payload
    assert "Inbox" in fake.folders


def test_webpage_downloader_names_extensionless_urls_from_the_response_type(monkeypatch):
    png = b"\x89PNG\r\n\x1a\nrest"
    patch_fetch(
        monkeypatch,
        WebResponse(
            png,
            "image/png",
            "https://images.example/render?id=9",
        ),
    )
    tools, fake = tool_with()

    tools.webpage_downloader("https://images.example/render?id=9")

    name = f"download-{date.today().isoformat()}.png"
    assert fake.files[name] == png


def test_webpage_downloader_follows_the_final_url_extension(monkeypatch):
    patch_fetch(
        monkeypatch,
        WebResponse(b"plain text", "text/plain", "https://example.com/moved/notes.txt"),
    )
    tools, fake = tool_with()

    tools.webpage_downloader("https://example.com/start")

    name = f"download-{date.today().isoformat()}.txt"
    assert fake.files[name] == b"plain text"


def test_webpage_downloader_rejects_bad_urls_without_uploading(monkeypatch):
    def fail_get(*_args, **_kwargs):
        raise AssertionError("the URL should not be fetched")

    monkeypatch.setattr("nextcloud.requests.get", fail_get)
    tools, fake = tool_with(folders={"Inbox/page.htm"})

    assert tools.webpage_downloader("").startswith("Webpage download failed: provide a URL")
    assert tools.webpage_downloader("ftp://example.com/file").startswith(
        "Webpage download failed: provide a full http or https URL"
    )
    assert tools.webpage_downloader("https://example.com/file", "Inbox/../secret.txt").startswith(
        "Webpage download failed:"
    )
    assert tools.webpage_downloader("https://example.com/file", "Inbox/page.htm").startswith(
        "Webpage download failed: Inbox/page.htm is a folder"
    )
    assert fake.puts == []


def test_webpage_downloader_reports_fetch_and_upload_failures(monkeypatch):
    tools, fake = tool_with()
    patch_fetch(monkeypatch, error=requests.ConnectionError("timed out"))
    assert tools.webpage_downloader("https://example.com/down").startswith(
        "Webpage download failed: could not fetch the URL"
    )
    assert fake.puts == []

    patch_fetch(
        monkeypatch,
        WebResponse(b"nope", "text/html", "https://example.com/missing", status_code=404),
    )
    assert (
        tools.webpage_downloader("https://example.com/missing")
        == "Webpage download failed: HTTP 404"
    )
    assert fake.puts == []

    patch_fetch(
        monkeypatch,
        WebResponse(b"<html><p>Hi</p></html>", "text/html", "https://example.com/page.html"),
    )
    fake.status_override = 401
    assert "authentication failed" in tools.webpage_downloader("https://example.com/page.html")
    assert fake.puts == []
