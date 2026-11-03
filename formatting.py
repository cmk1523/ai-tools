"""Convert text to and from hexadecimal, Base64, and JSON."""

from __future__ import annotations

import base64
import csv
import io
import json


def to_hex(text: str) -> str:
    """Return the uppercase hexadecimal encoding of text."""
    return text.encode("utf-8").hex().upper()


def from_hex(value: str) -> str:
    """Return the text decoded from a hexadecimal string."""
    return bytes.fromhex(value).decode("utf-8")


def to_base64(text: str) -> str:
    """Return the Base64 encoding of text."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def from_base64(value: str) -> str:
    """Return the text decoded from a Base64 string."""
    return base64.b64decode(value).decode("utf-8")


def csv_to_json(text: str) -> str:
    """Return JSON for CSV text, using the first row as field names."""
    reader = csv.DictReader(io.StringIO(text))
    return json.dumps(list(reader), indent=2)


def _csv_list_item(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return _csv_scalar(value)


def _csv_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _csv_cell(value: object) -> str:
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, list):
        return ";".join(_csv_list_item(item) for item in value)
    return _csv_scalar(value)


def json_to_csv(text: str) -> str:
    """Return CSV for a JSON object or array of objects.

    List values are joined with semicolons. Object values are stored as JSON strings.
    """
    data = json.loads(text)
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = data
    else:
        raise TypeError("JSON must be an object or an array of objects")

    if any(not isinstance(row, dict) for row in rows):
        raise TypeError("JSON array items must be objects")

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_cell(row.get(key)) for key in fieldnames})
    return buffer.getvalue()
