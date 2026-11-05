"""
title: Nextcloud
description: List, read, write, and delete files in Nextcloud over WebDAV, gzip compress or decompress one file, extract CNN Lite headlines to CSV, and download any URL into Nextcloud.
requirements: beautifulsoup4, requests
version: 0.4.0
"""

import csv
import gzip
import io
import mimetypes
import re
import tempfile
import xml.etree.ElementTree as ET
import zlib
from datetime import date
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment
from pydantic import BaseModel, Field

DAV = "{DAV:}"

PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname />
    <d:getcontentlength />
    <d:getlastmodified />
    <d:resourcetype />
  </d:prop>
</d:propfind>
"""

# zlib level 9 is the slowest, smallest DEFLATE setting.
_GZIP_LEVEL = zlib.Z_BEST_COMPRESSION
# Memory level 9 gives zlib the most memory for finding matches.
# The gzip module defaults to 8, which can leave a file slightly larger.
_GZIP_MEM_LEVEL = 9
_CHUNK_SIZE = 1024 * 1024
_HTML_SUFFIXES = {".htm", ".html"}
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# Extensions for common types when the URL path has none.
_TYPE_EXTENSIONS = {
    "text/html": "htm",
    "application/xhtml+xml": "htm",
    "text/plain": "txt",
    "text/css": "css",
    "text/csv": "csv",
    "application/json": "json",
    "application/pdf": "pdf",
    "application/zip": "zip",
    "application/gzip": "gz",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "audio/mpeg": "mp3",
    "video/mp4": "mp4",
}


class Tools:
    class Valves(BaseModel):
        NEXTCLOUD_URL: str = Field(
            default="http://nextcloud:80",
            description="Base URL of the Nextcloud server",
        )
        NEXTCLOUD_USER: str = Field(
            default="admin",
            description="Nextcloud username",
        )
        NEXTCLOUD_PASS: str = Field(
            default="admin",
            description="Nextcloud password (an app password is recommended)",
        )
        NEXTCLOUD_MAX_DOWNLOAD_SIZE: int = Field(
            default=1_048_576,
            description="Maximum number of bytes to download from a file (1MB)",
        )

    def __init__(self):
        """
        Load the Nextcloud connection settings.

        :return: None.
        """
        self.valves = self.Valves()

    def _session(self) -> requests.Session:
        """
        Open an HTTP session authenticated with the configured username and password.

        :return: A requests session for WebDAV calls.
        """
        session = requests.Session()
        session.auth = (self.valves.NEXTCLOUD_USER, self.valves.NEXTCLOUD_PASS)
        return session

    def _webdav_url(self, remote_path: str) -> str:
        """
        Build the WebDAV URL for a path in the user's files.

        :param remote_path: Path relative to the user's files.
        :return: The absolute WebDAV URL.
        """
        parts = [quote(p) for p in remote_path.strip("/").split("/") if p]
        base = self.valves.NEXTCLOUD_URL.rstrip("/")
        user = quote(self.valves.NEXTCLOUD_USER)
        return f"{base}/remote.php/dav/files/{user}/" + "/".join(parts)

    def _ensure_folders(self, session: requests.Session, remote_path: str) -> None:
        """
        Create each parent folder (MKCOL), ignoring ones that already exist.

        :param session: Authenticated session used for the WebDAV calls.
        :param remote_path: File path whose parent folders should exist.
        :return: None.
        """
        current = ""
        for part in PurePosixPath(remote_path).parent.parts:
            if part in ("", "/"):
                continue
            current = f"{current}/{part}" if current else part
            resp = session.request("MKCOL", self._webdav_url(current), timeout=30)
            # 201 = created, 405 = already exists
            if resp.status_code not in (201, 405):
                resp.raise_for_status()

    def _normalize_remote_path(self, remote_path: str) -> str:
        """
        Turn a user path into a relative Nextcloud file path.

        :param remote_path: Path relative to the user's files.
        :return: The path with leading slashes and empty segments removed.
        """
        if not isinstance(remote_path, str):
            raise TypeError("path must be str")
        parts = []
        for part in remote_path.strip().split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError("Path must not contain '..'")
            parts.append(part)
        if not parts:
            raise ValueError("Provide a file path, not the root folder")
        return "/".join(parts)

    def _output_remote_path(self, source: str, output_path: str, mode: str) -> str:
        """
        Choose the Nextcloud path to write.

        An empty output path adds .gz when compressing. When decompressing, it
        removes a .gz suffix, or adds .out when the name has none.

        :param source: Normalized path of the file being read.
        :param output_path: Requested destination. Empty means generate one.
        :param mode: ``compress`` or ``decompress``.
        :return: Normalized destination path.
        """
        if output_path is None:
            output_path = ""
        if not isinstance(output_path, str):
            raise TypeError("output_path must be str")
        chosen = output_path.strip()
        if chosen:
            return self._normalize_remote_path(chosen)

        path = PurePosixPath(source)
        if mode == "compress":
            return str(path.with_name(path.name + ".gz"))
        if path.name.lower().endswith(".gz") and len(path.name) > 3:
            return str(path.with_name(path.name[:-3]))
        return str(path.with_name(path.name + ".out"))

    def _csv_output_path(self, source: str, output_path: str) -> str:
        """
        Choose the Nextcloud path for a headline CSV.

        An empty output path writes <name>.csv beside the HTML file.

        :param source: Normalized path of the HTML file.
        :param output_path: Requested destination. Empty means generate one.
        :return: Normalized destination path.
        """
        if output_path is None:
            output_path = ""
        if not isinstance(output_path, str):
            raise TypeError("output_path must be str")
        chosen = output_path.strip()
        if chosen:
            return self._normalize_remote_path(chosen)
        return str(PurePosixPath(source).with_suffix(".csv"))

    def _stat_remote(
        self, session: requests.Session, remote_path: str
    ) -> tuple[str, str | None]:
        """
        Check whether a Nextcloud path is a file, a folder, or missing.

        :param session: Authenticated session used for the WebDAV call.
        :param remote_path: Path relative to the user's files.
        :return: A status of file, folder, missing, or error, plus an error detail.
        """
        try:
            info = session.request(
                "PROPFIND",
                self._webdav_url(remote_path),
                data=PROPFIND_BODY.encode("utf-8"),
                headers={"Depth": "0", "Content-Type": "application/xml"},
                timeout=30,
            )
        except requests.RequestException as e:
            return "error", f"could not reach Nextcloud ({e})"

        if info.status_code == 401:
            return "error", "authentication failed, check the username and password valves."
        if info.status_code == 404:
            return "missing", None
        if info.status_code != 207:
            return "error", f"HTTP {info.status_code}"

        try:
            root = ET.fromstring(info.content)
        except ET.ParseError as e:
            return "error", f"Nextcloud returned unreadable WebDAV XML ({e})"

        response = root.find(f"{DAV}response")
        prop = self._prop(response) if response is not None else None
        if prop is not None and prop.find(f"{DAV}resourcetype/{DAV}collection") is not None:
            return "folder", None
        return "file", None

    def _download_to_path(
        self, session: requests.Session, remote_path: str, local_path: Path
    ) -> str | None:
        """
        Download one Nextcloud file to a local path.

        :param session: Authenticated session used for the WebDAV call.
        :param remote_path: Path relative to the user's files.
        :param local_path: Where to write the downloaded bytes.
        :return: An error detail, or None when the download succeeded.
        """
        try:
            resp = session.get(self._webdav_url(remote_path), timeout=120, stream=True)
        except requests.RequestException as e:
            return f"could not reach Nextcloud ({e})"

        try:
            if resp.status_code == 401:
                return "authentication failed, check the username and password valves."
            if resp.status_code == 404:
                return f"{remote_path} was not found."
            if resp.status_code != 200:
                return f"HTTP {resp.status_code}"
            with local_path.open("wb") as outfile:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        outfile.write(chunk)
        except requests.RequestException as e:
            return f"could not reach Nextcloud ({e})"
        finally:
            resp.close()
        return None

    def _upload_from_path(
        self, session: requests.Session, remote_path: str, local_path: Path
    ) -> tuple[int | None, str | None]:
        """
        Upload a local file to Nextcloud, creating missing folders.

        :param session: Authenticated session used for the WebDAV calls.
        :param remote_path: Destination path relative to the user's files.
        :param local_path: File to upload.
        :return: The HTTP status and None, or None and an error detail.
        """
        try:
            self._ensure_folders(session, remote_path)
            with local_path.open("rb") as body:
                resp = session.put(
                    self._webdav_url(remote_path),
                    data=body,
                    timeout=120,
                )
        except requests.RequestException as e:
            return None, f"could not reach Nextcloud ({e})"

        if resp.status_code in (201, 204):
            return resp.status_code, None
        if resp.status_code == 401:
            return None, "authentication failed, check the username and password valves."
        return None, f"HTTP {resp.status_code}"

    @staticmethod
    def _gzip_compress_file(source: Path, destination: Path) -> None:
        """
        Compress one local file with gzip at the highest compression level.

        :param source: File to read.
        :param destination: File that receives the gzip bytes.
        :return: None.
        """
        compressor = zlib.compressobj(
            _GZIP_LEVEL,
            zlib.DEFLATED,
            16 + zlib.MAX_WBITS,
            _GZIP_MEM_LEVEL,
        )
        with source.open("rb") as infile, destination.open("wb") as outfile:
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

    @staticmethod
    def _gzip_decompress_file(source: Path, destination: Path) -> None:
        """
        Decompress one local gzip file.

        :param source: Gzip file to read.
        :param destination: File that receives the decompressed bytes.
        :return: None.
        """
        with gzip.open(source, "rb") as infile, destination.open("wb") as outfile:
            while True:
                chunk = infile.read(_CHUNK_SIZE)
                if not chunk:
                    break
                outfile.write(chunk)

    @staticmethod
    def _format_size(length: str) -> str:
        """
        Turn a byte count into a short size such as "1.5 KB".

        :param length: Byte count as text.
        :return: A readable size, or an empty string when the count is not a number.
        """
        try:
            size = int(length)
        except (TypeError, ValueError):
            return ""
        units = ("B", "KB", "MB", "GB")
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"

    @staticmethod
    def _prop(response: ET.Element) -> ET.Element | None:
        """
        Read the successful property set from a WebDAV response.

        :param response: One DAV response element.
        :return: The prop element with status 200, or None when there is none.
        """
        for propstat in response.findall(f"{DAV}propstat"):
            status = propstat.findtext(f"{DAV}status") or ""
            if " 200 " in status:
                return propstat.find(f"{DAV}prop")
        return None

    def list_files(self, remote_path: str = "") -> str:
        """
        List files and folders in the user's Nextcloud storage.
        Pass an empty path to list the root, or a folder path such as "Notes" to list that folder.
        This lists one level only; call again with a subfolder path to look inside it.

        :param remote_path: Folder path relative to the user's files. Leave empty for the root.
        :return: A text listing of names, types, and sizes, or an error message.
        """
        url = self._webdav_url(remote_path)
        session = self._session()

        try:
            resp = session.request(
                "PROPFIND",
                url,
                data=PROPFIND_BODY.encode("utf-8"),
                headers={"Depth": "1", "Content-Type": "application/xml"},
                timeout=30,
            )
        except requests.RequestException as e:
            return f"List failed: could not reach Nextcloud ({e})"

        if resp.status_code == 401:
            return "List failed: authentication failed, check the username and password valves."
        if resp.status_code == 404:
            return f"List failed: {remote_path or '/'} was not found."
        if resp.status_code != 207:
            return f"List failed: HTTP {resp.status_code}"

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            return f"List failed: Nextcloud returned unreadable WebDAV XML ({e})"

        requested = unquote(urlparse(url).path).rstrip("/")
        lines = []
        for response in root.findall(f"{DAV}response"):
            href = unquote(response.findtext(f"{DAV}href") or "").rstrip("/")
            if href == requested:
                continue
            prop = self._prop(response)
            if prop is None:
                continue
            name = (prop.findtext(f"{DAV}displayname") or "").strip()
            if not name:
                name = href.rsplit("/", 1)[-1] or href
            is_dir = prop.find(f"{DAV}resourcetype/{DAV}collection") is not None
            if is_dir:
                lines.append(f"{name}/  folder")
                continue
            size = self._format_size(prop.findtext(f"{DAV}getcontentlength") or "")
            modified = (prop.findtext(f"{DAV}getlastmodified") or "").strip()
            details = "  ".join(part for part in (size, modified) if part)
            lines.append(f"{name}  {details}".rstrip())

        folder = remote_path.strip("/") or "/"
        if not lines:
            return f"{folder} is empty."
        header = f"{folder} ({len(lines)} items)"
        return header + "\n" + "\n".join(lines)

    def download_file(self, remote_path: str) -> str:
        """
        Read a file from the user's Nextcloud storage and return its text contents.
        Downloads stop at NEXTCLOUD_MAX_DOWNLOAD_SIZE bytes (1MB by default). Binary files are reported by type and size instead of being returned.

        :param remote_path: Path relative to the user's files, e.g. "Notes/meeting.txt".
        :return: The file text, or an error message.
        """
        if not remote_path.strip("/"):
            return "Download failed: provide a file path, not the root folder."

        max_bytes = int(self.valves.NEXTCLOUD_MAX_DOWNLOAD_SIZE)
        if max_bytes < 1:
            return (
                "Download failed: NEXTCLOUD_MAX_DOWNLOAD_SIZE must be at least 1 byte."
            )

        session = self._session()
        try:
            resp = session.get(self._webdav_url(remote_path), timeout=60, stream=True)
        except requests.RequestException as e:
            return f"Download failed: could not reach Nextcloud ({e})"

        try:
            if resp.status_code == 401:
                return "Download failed: authentication failed, check the username and password valves."
            if resp.status_code == 404:
                return f"Download failed: {remote_path} was not found."
            if resp.status_code != 200:
                return f"Download failed: HTTP {resp.status_code}"

            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            reported = resp.headers.get("content-length", "").strip()
            # Read one extra byte so a file of exactly max_bytes is not treated as truncated.
            cap = max_bytes + 1
            chunks = []
            read = 0
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                remaining = cap - read
                if len(chunk) > remaining:
                    chunks.append(chunk[:remaining])
                    break
                chunks.append(chunk)
                read += len(chunk)
        except requests.RequestException as e:
            return f"Download failed: could not reach Nextcloud ({e})"
        finally:
            resp.close()

        raw = b"".join(chunks)
        truncated = len(raw) > max_bytes or (
            reported.isdigit() and int(reported) > max_bytes
        )
        if truncated:
            raw = raw[:max_bytes]
        if b"\x00" in raw[:1024]:
            size = self._format_size(reported or str(len(raw)))
            kind = content_type or "binary"
            return f"{remote_path} is a {kind} file ({size}) and has no text contents to return."

        text = raw.decode("utf-8", errors="replace")
        if truncated:
            limit = self._format_size(str(max_bytes))
            total = (
                self._format_size(reported) if reported.isdigit() else "a larger file"
            )
            return text + f"\n\n[Content truncated to {limit}; file is {total}]"
        return text

    def upload_file(self, remote_path: str, content: str) -> str:
        """
        Write a text file to the user's Nextcloud storage.
        Missing folders are created, and an existing file at the same path is overwritten.

        :param remote_path: Destination path relative to the root of the user's Nextcloud files, e.g. "Notes/meeting.txt".
        :param content: The text content to write to the file.
        :return: A message saying whether the upload succeeded.
        """
        session = self._session()

        try:
            self._ensure_folders(session, remote_path)
            resp = session.put(
                self._webdav_url(remote_path),
                data=content.encode("utf-8"),
                timeout=60,
            )
        except requests.RequestException as e:
            return f"Upload failed: could not reach Nextcloud ({e})"

        if resp.status_code == 201:
            return f"Created {remote_path} in Nextcloud."
        if resp.status_code == 204:
            return f"Overwrote existing {remote_path} in Nextcloud."
        if resp.status_code == 401:
            return "Upload failed: authentication failed, check the username and password valves."
        return f"Upload failed: HTTP {resp.status_code}"

    def delete_file(self, remote_path: str) -> str:
        """
        Delete a file from the user's Nextcloud storage.
        Folders are not removed. Pass the full file path, such as "Notes/meeting.txt".

        :param remote_path: Path relative to the user's files, e.g. "Notes/meeting.txt".
        :return: A message saying whether the delete succeeded.
        """
        if not remote_path.strip("/"):
            return "Delete failed: provide a file path, not the root folder."

        session = self._session()
        url = self._webdav_url(remote_path)

        try:
            info = session.request(
                "PROPFIND",
                url,
                data=PROPFIND_BODY.encode("utf-8"),
                headers={"Depth": "0", "Content-Type": "application/xml"},
                timeout=30,
            )
        except requests.RequestException as e:
            return f"Delete failed: could not reach Nextcloud ({e})"

        if info.status_code == 401:
            return "Delete failed: authentication failed, check the username and password valves."
        if info.status_code == 404:
            return f"Delete failed: {remote_path} was not found."
        if info.status_code != 207:
            return f"Delete failed: HTTP {info.status_code}"

        try:
            root = ET.fromstring(info.content)
        except ET.ParseError as e:
            return f"Delete failed: Nextcloud returned unreadable WebDAV XML ({e})"

        response = root.find(f"{DAV}response")
        prop = self._prop(response) if response is not None else None
        if prop is not None and prop.find(f"{DAV}resourcetype/{DAV}collection") is not None:
            return f"Delete failed: {remote_path} is a folder. This method deletes files only."

        try:
            resp = session.request("DELETE", url, timeout=30)
        except requests.RequestException as e:
            return f"Delete failed: could not reach Nextcloud ({e})"

        if resp.status_code in (200, 204):
            return f"Deleted {remote_path} from Nextcloud."
        if resp.status_code == 401:
            return "Delete failed: authentication failed, check the username and password valves."
        if resp.status_code == 404:
            return f"Delete failed: {remote_path} was not found."
        return f"Delete failed: HTTP {resp.status_code}"

    def _transfer_gzip(self, remote_path: str, output_path: str, mode: str) -> str:
        """
        Download one Nextcloud file, gzip compress or decompress it, and upload the result.

        The original file is left in place. The whole file is transferred because the
        bytes are written back to Nextcloud and are not returned. An existing file at
        the destination is overwritten.

        :param remote_path: Path of the file to read.
        :param output_path: Destination path. Empty means generate one beside the source.
        :param mode: ``compress`` or ``decompress``.
        :return: The source and destination paths and their sizes, or an error message.
        """
        action = "Compress" if mode == "compress" else "Decompress"
        try:
            source = self._normalize_remote_path(remote_path)
            destination = self._output_remote_path(source, output_path, mode)
        except (TypeError, ValueError) as exc:
            return f"{action} failed: {exc}"

        if destination == source:
            return f"{action} failed: output path is the same as the source file."

        session = self._session()
        kind, error = self._stat_remote(session, source)
        if error:
            return f"{action} failed: {error}"
        if kind == "missing":
            return f"{action} failed: {source} was not found."
        if kind == "folder":
            return (
                f"{action} failed: {source} is a folder. "
                "This method works on a single file."
            )

        dest_kind, dest_error = self._stat_remote(session, destination)
        if dest_error:
            return f"{action} failed: {dest_error}"
        if dest_kind == "folder":
            return f"{action} failed: {destination} is a folder."

        with tempfile.TemporaryDirectory() as temporary:
            local_source = Path(temporary) / "source"
            local_destination = Path(temporary) / "destination"
            download_error = self._download_to_path(session, source, local_source)
            if download_error:
                return f"{action} failed: {download_error}"

            source_size = local_source.stat().st_size
            try:
                if mode == "compress":
                    self._gzip_compress_file(local_source, local_destination)
                else:
                    self._gzip_decompress_file(local_source, local_destination)
            except gzip.BadGzipFile:
                return f"{action} failed: {source} is not a valid gzip file."
            except (EOFError, zlib.error):
                return f"{action} failed: {source} is not a complete gzip file."

            destination_size = local_destination.stat().st_size
            _status, upload_error = self._upload_from_path(
                session, destination, local_destination
            )

        if upload_error:
            return f"{action} failed: {upload_error}"

        verb = "Compressed" if mode == "compress" else "Decompressed"
        message = (
            f"{verb} {source} ({source_size} bytes) "
            f"to {destination} ({destination_size} bytes) in Nextcloud."
        )
        if dest_kind == "file":
            return message + " The existing file was overwritten."
        return message

    def compress_file(self, remote_path: str, output_path: str = "") -> str:
        """
        Compress one Nextcloud file with gzip at the highest compression level.

        The original file is left in place. Pass output_path to choose the new
        file path. When output_path is omitted or empty, the gzip file is written
        beside the original as <name>.gz. An existing file at the destination is
        overwritten. Compression uses zlib level 9 and memory level 9, which is
        the strongest gzip the standard library can produce.

        :param remote_path: Path relative to the user's files, e.g. "Notes/report.txt".
        :param output_path: New Nextcloud file path. Leave empty to generate <name>.gz.
        :return: The source and destination paths and their sizes, or an error message.
        """
        return self._transfer_gzip(remote_path, output_path, "compress")

    def decompress_file(self, remote_path: str, output_path: str = "") -> str:
        """
        Decompress one gzip file stored in Nextcloud.

        The gzip file is left in place. Pass output_path to choose the new file
        path. When output_path is omitted or empty, a name ending in .gz is written
        without that suffix, and any other name is written with .out added.
        An existing file at the destination is overwritten.

        :param remote_path: Path relative to the user's files, e.g. "Notes/report.txt.gz".
        :param output_path: New Nextcloud file path. Leave empty to generate one.
        :return: The source and destination paths and their sizes, or an error message.
        """
        return self._transfer_gzip(remote_path, output_path, "decompress")

    def extract_cnn_headlines(self, remote_path: str, output_path: str = "") -> str:
        """
        Read a CNN Lite .htm or .html file from Nextcloud and store its headlines as CSV.

        The CSV columns are headline, url, and date. Root-relative links become
        https://www.cnn.com URLs, and every date is written as YYYY-MM-DD. The HTML
        file is left in place. Pass output_path to choose the new file path. When
        output_path is omitted or empty, the CSV is written beside the HTML file
        with a .csv suffix. An existing file at the destination is overwritten.

        :param remote_path: Path relative to the user's files, e.g. "Inbox/cnn.htm".
        :param output_path: New Nextcloud file path. Leave empty to generate <name>.csv.
        :return: The source and destination paths and the headline count, or an error message.
        """
        try:
            source = self._normalize_remote_path(remote_path)
            destination = self._csv_output_path(source, output_path)
        except (TypeError, ValueError) as exc:
            return f"Extract failed: {exc}"

        if PurePosixPath(source).suffix.lower() not in _HTML_SUFFIXES:
            return f"Extract failed: {source} must be a .htm or .html file."
        if destination == source:
            return "Extract failed: output path is the same as the source file."

        session = self._session()
        kind, error = self._stat_remote(session, source)
        if error:
            return f"Extract failed: {error}"
        if kind == "missing":
            return f"Extract failed: {source} was not found."
        if kind == "folder":
            return (
                f"Extract failed: {source} is a folder. "
                "This method works on a single file."
            )

        dest_kind, dest_error = self._stat_remote(session, destination)
        if dest_error:
            return f"Extract failed: {dest_error}"
        if dest_kind == "folder":
            return f"Extract failed: {destination} is a folder."

        with tempfile.TemporaryDirectory() as temporary:
            local_html = Path(temporary) / f"source{PurePosixPath(source).suffix}"
            download_error = self._download_to_path(session, source, local_html)
            if download_error:
                return f"Extract failed: {download_error}"
            try:
                csv_text = _cnn_extractor().extract_headlines(str(local_html))
            except ValueError as exc:
                detail = str(exc)
                if detail.startswith("File is not UTF-8"):
                    detail = f"{source} is not UTF-8."
                return f"Extract failed: {detail}"

            local_csv = Path(temporary) / "headlines.csv"
            local_csv.write_bytes(csv_text.encode("utf-8"))
            headline_count = max(csv_text.count("\n") - 1, 0)
            _status, upload_error = self._upload_from_path(
                session, destination, local_csv
            )

        if upload_error:
            return f"Extract failed: {upload_error}"

        label = "headline" if headline_count == 1 else "headlines"
        message = (
            f"Extracted {headline_count} {label} from {source} "
            f"to {destination} in Nextcloud."
        )
        if dest_kind == "file":
            return message + " The existing file was overwritten."
        return message

    def webpage_downloader(self, url: str, output_path: str = "") -> str:
        """
        Download any HTTP or HTTPS URL and store the response in Nextcloud.

        HTML pages have CSS, scripts, and inline event handlers removed before
        they are saved. Every other response, including images, documents, and
        other files, is stored as received. Pass output_path to choose the
        Nextcloud file path. When output_path is omitted or empty, the file is
        named download-YYYY-MM-DD.ext. The extension comes from the URL path,
        or from the response type when the path has none. An existing file at
        the destination is overwritten.

        :param url: The full HTTP or HTTPS URL to fetch.
        :param output_path: Nextcloud file path. Leave empty to generate download-YYYY-MM-DD.ext.
        :return: The URL, destination path, and size, or an error message.
        """
        try:
            requested = self._require_http_url(url)
            chosen = self._optional_output_path(output_path)
        except (TypeError, ValueError) as exc:
            return f"Webpage download failed: {exc}"

        session = self._session()
        dest_kind = "missing"
        if chosen:
            dest_kind, blocked = self._destination_block(session, chosen)
            if blocked:
                return blocked

        with tempfile.TemporaryDirectory() as temporary:
            local_file = Path(temporary) / "download"
            fetched, error = self._fetch_url(requested, local_file)
            if error:
                return f"Webpage download failed: {error}"
            final_url, content_type = fetched
            is_html = self._is_html_file(content_type, local_file)
            if not chosen:
                chosen = self._webpage_remote_path(final_url, content_type, is_html)
                dest_kind, blocked = self._destination_block(session, chosen)
                if blocked:
                    return blocked

            if is_html:
                self._strip_html_file(local_file, content_type)

            size = local_file.stat().st_size
            _status, upload_error = self._upload_from_path(session, chosen, local_file)

        if upload_error:
            return f"Webpage download failed: {upload_error}"

        message = f"Downloaded {requested} ({size} bytes) to {chosen} in Nextcloud."
        if dest_kind == "file":
            return message + " The existing file was overwritten."
        return message

    def _destination_block(
        self, session: requests.Session, remote_path: str
    ) -> tuple[str, str | None]:
        """
        Reject a destination that cannot receive a downloaded file.

        :param session: Authenticated session used for the WebDAV call.
        :param remote_path: Destination path relative to the user's files.
        :return: The path status, and an error message when the path cannot be used.
        """
        kind, error = self._stat_remote(session, remote_path)
        if error:
            return kind, f"Webpage download failed: {error}"
        if kind == "folder":
            return kind, f"Webpage download failed: {remote_path} is a folder."
        return kind, None

    @staticmethod
    def _require_http_url(url: str) -> str:
        """
        Require a full HTTP or HTTPS URL.

        :param url: The address to check.
        :return: The URL with surrounding whitespace removed.
        """
        if not isinstance(url, str):
            raise TypeError("url must be str")
        requested = url.strip()
        if not requested:
            raise ValueError("provide a URL")
        parsed = urlparse(requested)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("provide a full http or https URL")
        return requested

    def _optional_output_path(self, output_path: str) -> str:
        """
        Normalize a requested Nextcloud path, or return empty when one was not given.

        :param output_path: Requested destination. Empty means generate one later.
        :return: A normalized path, or an empty string.
        """
        if output_path is None:
            return ""
        if not isinstance(output_path, str):
            raise TypeError("output_path must be str")
        if not output_path.strip():
            return ""
        return self._normalize_remote_path(output_path)

    @staticmethod
    def _fetch_url(
        url: str, local_path: Path
    ) -> tuple[tuple[str, str] | None, str | None]:
        """
        Download any HTTP or HTTPS URL to a local file.

        The Nextcloud session is not used, so its credentials are not sent to
        the remote site.

        :param url: The full URL to fetch.
        :param local_path: Where to write the response body.
        :return: The final URL and content type, plus None, or None and an error detail.
        """
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _BROWSER_USER_AGENT},
                timeout=60,
                stream=True,
                allow_redirects=True,
            )
        except requests.RequestException as e:
            return None, f"could not fetch the URL ({e})"

        try:
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            with local_path.open("wb") as outfile:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        outfile.write(chunk)
            content_type = resp.headers.get("content-type", "")
            return (resp.url or url, content_type), None
        except requests.RequestException as e:
            return None, f"could not fetch the URL ({e})"
        finally:
            resp.close()

    @staticmethod
    def _is_html_file(content_type: str, local_path: Path) -> bool:
        """
        Decide whether a downloaded body should be treated as HTML.

        :param content_type: The response Content-Type header.
        :param local_path: The downloaded file.
        :return: True when the type or the body is HTML.
        """
        media = content_type.split(";")[0].strip().lower()
        if "html" in media:
            return True
        with local_path.open("rb") as infile:
            start = infile.read(512).lstrip().lower()
        return start.startswith((b"<!doctype html", b"<html"))

    @staticmethod
    def _strip_css_and_scripts(html: str) -> str:
        """
        Remove CSS, scripts, and inline event handlers from HTML.

        :param html: The page HTML.
        :return: The HTML with styles and scripts removed.
        """
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        for link in soup.find_all("link"):
            rel = [item.lower() for item in (link.get("rel") or [])]
            if "stylesheet" in rel or "preload" in rel or "modulepreload" in rel:
                link.decompose()

        for tag in soup.find_all(True):
            tag.attrs.pop("style", None)
            for attr in [name for name in tag.attrs if name.lower().startswith("on")]:
                del tag.attrs[attr]

        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        return str(soup)

    def _strip_html_file(self, local_path: Path, content_type: str) -> None:
        """
        Replace a downloaded HTML file with a copy that has CSS and scripts removed.

        :param local_path: The downloaded file.
        :param content_type: The response Content-Type header, used for the charset.
        :return: None.
        """
        raw = local_path.read_bytes()
        text = self._decode_page(raw, content_type)
        local_path.write_bytes(self._strip_css_and_scripts(text).encode("utf-8"))

    @staticmethod
    def _decode_page(raw: bytes, content_type: str) -> str:
        """
        Decode a page body using the charset in Content-Type.

        :param raw: The response body.
        :param content_type: The response Content-Type header.
        :return: The page text.
        """
        charset = "utf-8"
        for part in content_type.split(";")[1:]:
            key, _sep, value = part.strip().partition("=")
            if key.lower() == "charset" and value.strip():
                charset = value.strip().strip("\"'")
                break
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _url_extension(url: str) -> str:
        """
        Read a short file extension from a URL path.

        :param url: The fetched URL.
        :return: The extension without the dot, or an empty string when there is none.
        """
        suffix = PurePosixPath(urlparse(url).path).suffix
        extension = suffix[1:]
        if extension.isalnum() and 1 <= len(extension) <= 10:
            return extension.lower()
        return ""

    @staticmethod
    def _extension_from_type(content_type: str) -> str:
        """
        Choose a file extension from a Content-Type value.

        :param content_type: The response Content-Type header.
        :return: An extension without the dot, or an empty string when the type is unknown.
        """
        media = content_type.split(";")[0].strip().lower()
        if media in _TYPE_EXTENSIONS:
            return _TYPE_EXTENSIONS[media]
        if not media or "/" not in media:
            return ""
        guessed = mimetypes.guess_extension(media, strict=False)
        if not guessed:
            return ""
        extension = guessed.lstrip(".").lower()
        if extension.isalnum() and len(extension) <= 10:
            return extension
        return ""

    def _webpage_remote_path(self, url: str, content_type: str, is_html: bool) -> str:
        """
        Build the generated Nextcloud file name for a downloaded URL.

        :param url: The final URL after redirects.
        :param content_type: The response Content-Type header.
        :param is_html: Whether the body is HTML.
        :return: A file name of the form download-YYYY-MM-DD.ext.
        """
        extension = self._url_extension(url) or self._extension_from_type(content_type)
        if not extension:
            extension = "htm" if is_html else "bin"
        return f"download-{date.today().isoformat()}.{extension}"


_CNN_ORIGIN = "https://www.cnn.com"
_CNN_COLUMNS = ("headline", "url", "date")
# Curly single quotes that should read as a straight apostrophe.
_SINGLE_QUOTE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
    }
)

# Article paths look like /2026/11/05/section/slug.
_URL_DATE_RE = re.compile(
    r"/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})(?=/|$|[?#])"
)
# Dates printed near the top of a saved CNN Lite page, earliest match wins.
_PAGE_DATE_RES = (
    re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})(?!\d)"),
)
_CNN_EXTRACTOR = None


def _iso_date(year: int, month: int, day: int) -> str | None:
    """
    Build a YYYY-MM-DD date when the calendar values are real.

    :param year: Full year.
    :param month: Month number.
    :param day: Day of the month.
    :return: The ISO date, or None when the values are not a real day.
    """
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _date_from_match(match: re.Match[str]) -> str | None:
    """
    Read named year, month, and day groups from a date match.

    :param match: A match from one of the date patterns.
    :return: The ISO date, or None when the groups are not a real day.
    """
    return _iso_date(int(match["year"]), int(match["month"]), int(match["day"]))


def _date_from_url(href: str) -> str | None:
    """
    Read the calendar date embedded in a CNN article path.

    :param href: Article href, relative or absolute.
    :return: The ISO date, or None when the path has no real date.
    """
    match = _URL_DATE_RE.search(href)
    if not match:
        return None
    return _date_from_match(match)


def _date_from_text(text: str) -> str | None:
    """
    Find the earliest recognizable date in a block of text.

    Slash dates written as M/D/YYYY are read as month, day, then year.

    :param text: Text that may contain a page date.
    :return: The ISO date, or None when no real date is present.
    """
    found: list[tuple[int, str]] = []
    for pattern in _PAGE_DATE_RES:
        match = pattern.search(text)
        if not match:
            continue
        iso = _date_from_match(match)
        if iso:
            found.append((match.start(), iso))
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    return found[0][1]


def _straight_single_quotes(text: str) -> str:
    """
    Turn curly single quotes into a straight apostrophe.

    :param text: Text that may contain typographic single quotes.
    :return: The same text with those quotes replaced.
    """
    return text.translate(_SINGLE_QUOTE_TRANSLATION)


def _is_story_href(href: str) -> bool:
    """
    Keep links that can point at a story.

    :param href: Raw href from an anchor.
    :return: True when the href is not a script, mail, or fragment link.
    """
    lowered = href.lower()
    return not lowered.startswith(("javascript:", "mailto:", "#"))


class _CnnLiteParser(HTMLParser):
    """Collect the CNN Lite header text and each story card."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.header_parts: list[str] = []
        self.stories: list[tuple[str, str]] = []
        self._in_header = False
        self._in_story = False
        self._href = ""
        self._headline_parts: list[str] = []

    def _finish_anchor(self) -> None:
        """Store the open story anchor, if it has a headline and an href."""
        if not self._href:
            return
        headline = _straight_single_quotes(
            " ".join("".join(self._headline_parts).split())
        )
        if headline:
            self.stories.append((headline, self._href))
        self._href = ""
        self._headline_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """
        Watch the lite header and the start of each story card.

        :param tag: Lowercased tag name.
        :param attrs: Attribute pairs from the tag.
        :return: None.
        """
        attr = {name.lower(): value or "" for name, value in attrs}
        classes = set(attr.get("class", "").split())
        if tag == "header" and "header--lite" in classes:
            self._in_header = True
        if tag == "li" and "card--lite" in classes:
            self._finish_anchor()
            self._in_story = True
        if tag == "a" and self._in_story:
            href = attr.get("href", "").strip()
            if href and _is_story_href(href):
                self._finish_anchor()
                self._href = href
                self._headline_parts = []

    def handle_endtag(self, tag: str) -> None:
        """
        Close the header, story anchor, or story card.

        :param tag: Lowercased tag name.
        :return: None.
        """
        if tag == "a":
            self._finish_anchor()
        elif tag == "li" and self._in_story:
            self._finish_anchor()
            self._in_story = False
        elif tag == "header":
            self._in_header = False

    def handle_data(self, data: str) -> None:
        """
        Collect header text and the headline inside the open story anchor.

        :param data: Decoded text node.
        :return: None.
        """
        if self._in_header:
            self.header_parts.append(data)
        if self._href:
            self._headline_parts.append(data)

    def close(self) -> None:
        """Flush the parser and keep a story whose closing tags were missing."""
        super().close()
        self._finish_anchor()


class _CnnLiteExtractor:
    """Parse a CNN Lite .htm or .html file into headline, URL, and date CSV."""

    def _source_file(self, path: str) -> Path:
        """
        Check that a path points at one .htm or .html file.

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
        if source.suffix.lower() not in _HTML_SUFFIXES:
            raise ValueError("File must be .htm or .html")
        return source

    def _page_date(self, html: str, parser: _CnnLiteParser) -> str | None:
        """
        Use the header date, then any date that appears before the first story.

        :param html: The full page HTML.
        :param parser: Parser that already collected the header text.
        :return: The page date in YYYY-MM-DD, or None when the page has none.
        """
        header_date = _date_from_text("".join(parser.header_parts))
        if header_date:
            return header_date
        card_at = html.find("card--lite")
        prefix = html if card_at < 0 else html[:card_at]
        return _date_from_text(prefix)

    def _rows(self, html: str) -> list[tuple[str, str, str]]:
        """
        Turn CNN Lite story cards into headline, absolute URL, and date rows.

        A date in the article path wins. Stories without one use the page date.

        :param html: The page HTML.
        :return: One tuple per story, in page order.
        """
        parser = _CnnLiteParser()
        parser.feed(html)
        parser.close()
        page_date = self._page_date(html, parser)
        rows: list[tuple[str, str, str]] = []
        for headline, href in parser.stories:
            story_date = _date_from_url(href) or page_date
            if not story_date:
                raise ValueError(
                    "No date near the top of the file, and this headline has none: "
                    + headline
                )
            rows.append((headline, urljoin(f"{_CNN_ORIGIN}/", href), story_date))
        return rows

    def extract_headlines(self, path: str) -> str:
        """
        Read a CNN Lite .htm or .html file and return its news headlines as CSV.

        The header row is headline,url,date. Root-relative links become
        https://www.cnn.com URLs. When an article URL contains a calendar date,
        that date is used. Otherwise the date near the top of the file is used.
        A page date written as M/D/YYYY is read as month, day, then year.
        Every date is written as YYYY-MM-DD. Curly single quotes in a
        headline are written as a straight apostrophe.

        :param path: Path to a .htm or .html file.
        :return: CSV text with one headline per row.
        """
        source = self._source_file(path)
        try:
            html = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"File is not UTF-8: {source}") from exc

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(_CNN_COLUMNS)
        writer.writerows(self._rows(html))
        return buffer.getvalue()


def _cnn_extractor() -> _CnnLiteExtractor:
    """
    Return the CNN Lite extractor built into this file.

    :return: An extractor instance.
    """
    global _CNN_EXTRACTOR
    if _CNN_EXTRACTOR is None:
        _CNN_EXTRACTOR = _CnnLiteExtractor()
    return _CNN_EXTRACTOR
