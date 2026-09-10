"""Unit tests for plugins/pressreliz tools and utilities."""

import pytest
from plugins.pressreliz.statind import _lucene_escape, _strip_figures, TOOL_SCHEMA as STATIND_SCHEMA
from plugins.pressreliz.data import TOOL_SCHEMA as DATA_SCHEMA
from plugins.pressreliz.sdmx import TOOL_SCHEMA as SDMX_SCHEMA


def test_lucene_escape():
    assert _lucene_escape("hello world") == "hello world"
    # Special characters should be backslash-escaped
    escaped = _lucene_escape("export (hajmi) + import - 2024")
    assert "\\(" in escaped and "\\)" in escaped
    assert "\\+" in escaped and "\\-" in escaped


def test_strip_figures():
    # Tokens starting with digits should be stripped
    text = "2024-yilda qishloq xo'jaligi 8,5 foizga o'sdi"
    stripped = _strip_figures(text)
    assert "2024" not in stripped
    assert "8,5" not in stripped
    assert "qishloq" in stripped
    assert "xo'jaligi" in stripped


def test_schemas_structure():
    for schema, expected_name in [
        (STATIND_SCHEMA, "statind_code"),
        (DATA_SCHEMA, "statind_data"),
        (SDMX_SCHEMA, "statind_data_url"),
    ]:
        assert isinstance(schema, dict)
        assert schema.get("name") == expected_name
        assert "description" in schema
        assert "parameters" in schema
        assert schema["parameters"].get("type") == "object"
