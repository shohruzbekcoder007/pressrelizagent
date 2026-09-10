"""Unit tests for plugins/telegram formatting and verification tool."""

import pytest
from plugins.telegram.post import (
    _utf16_len,
    _cells,
    _table_to_lines,
    _flatten,
    TOOL_SCHEMA,
)


def test_utf16_len():
    # Regular ascii character has length 1
    assert _utf16_len("abc") == 3
    # Emoji takes 2 utf-16 code units
    assert _utf16_len("📊") == 2
    assert _utf16_len("Hello 🇺🇿") > len("Hello ")


def test_cells_and_table_to_lines():
    header = ["Ko'rsatkich", "2024", "2025"]
    rows = [
        ["Eksport", "12 000,0", "14 500,0"],
        ["Import", "18 000,0", "20 100,0"],
    ]
    lines = _table_to_lines(header, rows)
    assert len(lines) == 2
    assert "• Eksport — 2024: 12 000,0, 2025: 14 500,0" in lines[0]


def test_flatten():
    markdown_text = (
        "## Sarlavha\n\n"
        "Oddiy matn 10%% o'sdi.\n\n"
        "| Hudud | Qiymat |\n"
        "|---|---|\n"
        "| Toshkent | 100 |\n"
    )
    flattened = _flatten(markdown_text)
    # Double percents removed
    assert "10%" in flattened
    assert "10%%" not in flattened
    # Table converted to bullet
    assert "• Toshkent — Qiymat: 100" in flattened


def test_telegram_post_schema():
    assert isinstance(TOOL_SCHEMA, dict)
    assert TOOL_SCHEMA.get("name") == "telegram_post"
    assert "parameters" in TOOL_SCHEMA
    props = TOOL_SCHEMA["parameters"].get("properties", {})
    assert "matn" in props
    assert "tasdiqlangan" in props
