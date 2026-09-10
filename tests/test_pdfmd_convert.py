"""Unit tests for plugins/pdfmd tool schemas."""

import pytest
from plugins.pdfmd.convert import TOOL_SCHEMA as CONVERT_SCHEMA
from plugins.pdfmd.extract import TOOL_SCHEMA as EXTRACT_SCHEMA


def test_pdfmd_schemas():
    assert isinstance(CONVERT_SCHEMA, dict)
    assert CONVERT_SCHEMA.get("name") == "pdf_to_md"
    assert "parameters" in CONVERT_SCHEMA

    assert isinstance(EXTRACT_SCHEMA, dict)
    assert EXTRACT_SCHEMA.get("name") == "pdf_extract"
    assert "parameters" in EXTRACT_SCHEMA
