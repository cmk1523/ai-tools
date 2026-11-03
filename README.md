# ai-tools

Small Python helpers for hashing, encryption, text formatting, and Nextcloud.

| Module | What it does |
| --- | --- |
| `hashing.py` | MD5 and SHA-1 digests of text or bytes |
| `encryption.py` | AES-GCM encrypt and decrypt |
| `formatting.py` | Hex, Base64, and CSV/JSON conversion |
| `nextcloud.py` | List, read, write, and delete files over WebDAV |
| `download-webpage-store-in-nextcloud.py` | Fetch a page, strip CSS and scripts, and save it to Nextcloud |

## Setup

```bash
pip install cryptography requests pydantic beautifulsoup4 httpx
```

## Tests

```bash
pytest
```
