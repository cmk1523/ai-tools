"""
title: Nextcloud
description: List, read, write, and delete files in Nextcloud over WebDAV using a username and password.
requirements: requests
version: 0.1.0
"""

from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

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
