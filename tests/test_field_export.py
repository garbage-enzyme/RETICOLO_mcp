"""Unit tests for field_export helpers — no MATLAB required."""

from __future__ import annotations

import pytest

from reticolo_mcp.field_export import _component_index


class TestComponentIndex:
    def test_ex(self):
        assert _component_index("Ex") == 0

    def test_ey(self):
        assert _component_index("Ey") == 1

    def test_ez(self):
        assert _component_index("Ez") == 2

    def test_hx(self):
        assert _component_index("Hx") == 3

    def test_hy(self):
        assert _component_index("Hy") == 4

    def test_hz(self):
        assert _component_index("Hz") == 5

    def test_unknown_returns_default(self):
        assert _component_index("Unknown") == 0

    def test_empty_string(self):
        assert _component_index("") == 0
