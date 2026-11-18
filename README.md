# ai-tools

[Open WebUI](https://github.com/open-webui/open-webui) tools for hashing, encryption, compression, text formatting, and Nextcloud. Each module defines a `Tools` class that Open WebUI loads directly.

| Module | What it does |
| --- | --- |
| `hashing.py` | MD5 and SHA-1 digests of text or bytes |
| `encryption.py` | Password encrypt and decrypt. Argon2id stretches the password into an AES-256-GCM key |
| `gzipfile.py` | Gzip compress and decompress one file at the highest compression level |
| `formatting.py` | Hex, Base64, and CSV/JSON conversion |
| `nextcloud.py` | List, read, write, and delete files over WebDAV, gzip compress or decompress one file, extract CNN Lite headlines to CSV, and download any URL into Nextcloud |

## Setup

```bash
pip install cryptography argon2-cffi requests pydantic beautifulsoup4
```

## Tests

```bash
pytest
```

## LM Studio MCP server

`lmstudio/lmstudio.js` is an MCP server (stdio) with three tools: `get_current_date`, `web_search` (DuckDuckGo Lite), and `json_to_csv`.

```bash
cd lmstudio
npm install
npm test
```
