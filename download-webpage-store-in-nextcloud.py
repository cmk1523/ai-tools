"""
title: Webpage Download to Nextcloud
description: Fetch a URL, strip CSS and scripts from HTML, and save the result to Nextcloud as download-YYYY-MM-DD.ext.
requirements: beautifulsoup4, httpx, requests
version: 1.0.0
"""

from datetime import date
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

import httpx
import requests
from bs4 import BeautifulSoup, Comment
from pydantic import BaseModel, Field


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

    def __init__(self):
        """
        Load the Nextcloud connection settings.

        :return: None.
        """
        self.valves = self.Valves()

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
            rel = [r.lower() for r in (link.get("rel") or [])]
            if "stylesheet" in rel or "preload" in rel or "modulepreload" in rel:
                link.decompose()

        for tag in soup.find_all(True):
            tag.attrs.pop("style", None)
            for attr in [a for a in tag.attrs if a.lower().startswith("on")]:
                del tag.attrs[attr]

        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()

        return str(soup)

    @staticmethod
    def _download_filename(url: str) -> str:
        """
        Build the Nextcloud file name for a downloaded page.
        The name is download-YYYY-MM-DD plus the URL path extension, or .htm when there is none.

        :param url: The page URL.
        :return: The file name to store in Nextcloud.
        """
        suffix = PurePosixPath(urlparse(url).path).suffix
        extension = suffix[1:] if len(suffix) > 1 else "htm"
        return f"download-{date.today().isoformat()}.{extension}"

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
            if resp.status_code not in (201, 405):
                resp.raise_for_status()

    def _upload_file(self, remote_path: str, content: str) -> str:
        """
        Write a text file to the user's Nextcloud storage.
        Missing folders are created, and an existing file at the same path is overwritten.

        :param remote_path: Destination path relative to the user's files.
        :param content: The text to write.
        :return: A message saying whether the upload succeeded.
        """
        session = requests.Session()
        session.auth = (self.valves.NEXTCLOUD_USER, self.valves.NEXTCLOUD_PASS)

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

    async def save_webpage_to_nextcloud(self, url: str) -> str:
        """
        Download a webpage, strip CSS and scripts from HTML, and store the result in Nextcloud.
        The file is named download-YYYY-MM-DD.ext, where the extension matches the URL path.
        Pages with no extension are saved as .htm. A file saved earlier the same day with the same extension is overwritten.

        :param url: The full HTTP or HTTPS URL to fetch.
        :return: Whether the file was saved, and its Nextcloud path.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
        except Exception as e:
            return f"An error occurred while trying to curl the page: {str(e)}"

        if response.status_code != 200:
            return f"Failed to fetch page. HTTP Status Code: {response.status_code}"

        text = response.text
        content_type = response.headers.get("content-type", "").lower()
        if "html" in content_type or text.lstrip()[:15].lower().startswith(
            ("<!doctype html", "<html")
        ):
            text = self._strip_css_and_scripts(text)

        remote_path = self._download_filename(url)
        result = self._upload_file(remote_path, text)
        return f"{result} ({len(text)} characters from {url})"
