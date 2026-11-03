# ai-tools

[Open WebUI](https://github.com/open-webui/open-webui) tools for hashing, encryption, text formatting, and Nextcloud. Each module defines a `Tools` class that Open WebUI loads directly.

| Module | What it does |
| --- | --- |
| `hashing.py` | MD5 and SHA-1 digests of text or bytes |
| `encryption.py` | Password encrypt and decrypt. Argon2id stretches the password into an AES-256-GCM key |
| `formatting.py` | Hex, Base64, and CSV/JSON conversion |
| `nextcloud.py` | List, read, write, and delete files over WebDAV |
| `download-webpage-store-in-nextcloud.py` | Fetch a page, strip CSS and scripts, and save it to Nextcloud |

## Setup

```bash
pip install cryptography argon2-cffi requests pydantic beautifulsoup4 httpx
```

## Tests

```bash
pytest
```
