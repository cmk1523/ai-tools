"""
title: Formatting
description: Convert text to and from hexadecimal and Base64, and convert between CSV and JSON.
version: 0.1.0
"""

from __future__ import annotations

import base64
import csv
import io
import json


class Tools:
    def __init__(self):
        pass

    def to_hex(self, text: str) -> str:
        """
        Encode text as uppercase hexadecimal.

        :param text: The text to encode.
        :return: The uppercase hexadecimal encoding of the text.
        """
        return text.encode("utf-8").hex().upper()

    def from_hex(self, value: str) -> str:
        """
        Decode a hexadecimal string to text.

        :param value: Hexadecimal text. Spaces between bytes are allowed.
        :return: The decoded text.
        """
        return bytes.fromhex(value).decode("utf-8")

    def to_base64(self, text: str) -> str:
        """
        Encode text as Base64.

        :param text: The text to encode.
        :return: The Base64 encoding of the text.
        """
        return base64.b64encode(text.encode("utf-8")).decode("ascii")

    def from_base64(self, value: str) -> str:
        """
        Decode a Base64 string to text.

        :param value: Base64 text.
        :return: The decoded text.
        """
        return base64.b64decode(value).decode("utf-8")

    def csv_to_json(self, text: str) -> str:
        """
        Convert CSV text to JSON, using the first row as field names.

        :param text: CSV text with a header row.
        :return: Pretty-printed JSON for the rows.
        """
        reader = csv.DictReader(io.StringIO(text))
        return json.dumps(list(reader), indent=2)

    def _csv_list_item(self, value: object) -> str:
        """
        Format one item from a list for a CSV cell.

        :param value: A list item. Nested objects and lists are stored as JSON.
        :return: The item as text for a semicolon-joined cell.
        """
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return self._csv_scalar(value)

    def _csv_scalar(self, value: object) -> str:
        """
        Format a scalar value for a CSV cell.

        :param value: A scalar. None becomes an empty string, and booleans become true or false.
        :return: The value as text.
        """
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _csv_cell(self, value: object) -> str:
        """
        Format one JSON value as a CSV cell.

        :param value: A cell value. Lists are joined with semicolons. Objects are stored as JSON.
        :return: The cell text.
        """
        if isinstance(value, dict):
            return json.dumps(value)
        if isinstance(value, list):
            return ";".join(self._csv_list_item(item) for item in value)
        return self._csv_scalar(value)

    def json_to_csv(self, text: str) -> str:
        """
        Convert a JSON object or array of objects to CSV.
        List values are joined with semicolons. Object values are stored as JSON strings.

        :param text: JSON text for one object or an array of objects.
        :return: CSV text with a header row.
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
            writer.writerow({key: self._csv_cell(row.get(key)) for key in fieldnames})
        return buffer.getvalue()
