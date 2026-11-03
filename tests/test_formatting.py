"""Tests for hexadecimal, Base64, CSV, and JSON helpers."""

import json

import pytest

from formatting import csv_to_json, from_base64, from_hex, json_to_csv, to_base64, to_hex


def test_hex_roundtrip_is_uppercase():
    assert to_hex("Hi") == "4869"
    assert to_hex("é") == "C3A9"
    assert from_hex("4869") == "Hi"
    assert from_hex("c3a9") == "é"
    assert from_hex("48 69") == "Hi"


def test_base64_roundtrip():
    assert to_base64("Hi") == "SGk="
    assert from_base64("SGk=") == "Hi"
    assert from_base64(to_base64("café")) == "café"
    assert to_base64("") == ""
    assert from_base64("") == ""


def test_hex_and_base64_reject_invalid_input():
    with pytest.raises(ValueError):
        from_hex("zz")
    with pytest.raises(ValueError):
        from_base64("abc")


def test_csv_to_json_uses_header_row():
    csv_text = "name,age\nAda,36\nGrace,41\n"
    parsed = json.loads(csv_to_json(csv_text))
    assert parsed == [
        {"name": "Ada", "age": "36"},
        {"name": "Grace", "age": "41"},
    ]
    assert csv_to_json(csv_text) == json.dumps(parsed, indent=2)


def test_csv_to_json_empty_input():
    assert json.loads(csv_to_json("")) == []


def test_json_object_and_array_become_csv():
    assert json_to_csv('{"a": 1}') == "a\n1\n"
    assert json_to_csv('[{"a": 1}, {"b": 2, "a": 3}]') == "a,b\n1,\n3,2\n"


def test_json_to_csv_formats_nested_and_scalar_values():
    text = json.dumps(
        [
            {
                "name": "Ada",
                "age": 36,
                "ok": True,
                "note": None,
                "tags": ["a", "b"],
                "meta": {"x": 1},
                "flags": [True, None, {"y": 2}],
            }
        ]
    )
    assert json_to_csv(text) == (
        "name,age,ok,note,tags,meta,flags\n"
        'Ada,36,true,,a;b,"{""x"": 1}","true;;{""y"": 2}"\n'
    )


def test_flat_csv_roundtrip():
    original = "name,city\nAda,London\n"
    restored = json_to_csv(csv_to_json(original))
    assert restored == original


def test_json_to_csv_rejects_non_objects():
    with pytest.raises(TypeError, match="object or an array"):
        json_to_csv('"hello"')
    with pytest.raises(TypeError, match="object or an array"):
        json_to_csv("42")
    with pytest.raises(TypeError, match="array items must be objects"):
        json_to_csv("[1, 2]")
