# ai-tools

[Open WebUI](https://github.com/open-webui/open-webui) tools for hashing, encryption, compression, text formatting, and Nextcloud. Each module defines a `Tools` class that Open WebUI loads directly.

| Module | What it does |
| --- | --- |
| `hashing.py` | MD5 and SHA-1 digests of text or bytes |
| `encryption.py` | Password encrypt and decrypt. Argon2id stretches the password into an AES-256-GCM key |
| `gzipfile.py` | Gzip compress and decompress one file at the highest compression level |
| `formatting.py` | Hex, Base64, and CSV/JSON conversion |
| `nextcloud.py` | List, read, write, and delete files over WebDAV, gzip compress or decompress one file, extract CNN Lite headlines to CSV, and download any URL into Nextcloud |
| `cnn-lite-news-extractor.py` | Read a CNN Lite `.htm` or `.html` file and return headlines, URLs, and dates as CSV |

## Setup

```bash
pip install cryptography argon2-cffi requests pydantic beautifulsoup4
```

## Tests

```bash
pytest
```
