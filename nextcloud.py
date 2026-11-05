"""
title: Nextcloud
description: List, read, write, and delete files in Nextcloud over WebDAV, and gzip compress or decompress one file.
requirements: requests
version: 0.2.0
"""

import gzip
import tempfile
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

import requests
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
