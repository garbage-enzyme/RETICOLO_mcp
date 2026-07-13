"""Unit tests for worker helpers — no MATLAB required."""

from __future__ import annotations

from reticolo_mcp.worker import _to_complex


class TestToComplex:
    def test_constant_n(self):
        result = _to_complex([1.5])
        assert result == [1.5]

    def test_complex_pair(self):
        result = _to_complex([[4.0, 0.001]])
        assert result == [complex(4.0, 0.001)]

    def test_patterned_layer(self):
        result = _to_complex([
            [1.0, [0.0, 0.0, 0.3, 0.3, 4.0, 0.001, 1]]
        ])
        assert len(result) == 1
        assert isinstance(result[0], list)
        assert result[0][0] == 1.0
        assert result[0][1] == [0.0, 0.0, 0.3, 0.3, 4.0, 0.001, 1]

    def test_mixed_complex_in_pattern(self):
        result = _to_complex([
            [[1.0, 0.0], [0.0, 0.0, 0.3, 0.3, [4.0, 0.001], 1]]
        ])
        assert len(result) == 1
        assert isinstance(result[0], list)
        assert result[0][0] == complex(1.0, 0.0)
        assert len(result[0][1]) == 6
        assert result[0][1][0:4] == [0.0, 0.0, 0.3, 0.3]
        assert result[0][1][4] == [4.0, 0.001]
        assert result[0][1][5] == 1

    def test_empty_list(self):
        assert _to_complex([]) == []

    def test_nested_list_no_complex(self):
        result = _to_complex([[1.0, [0.0, 0.0, 0.3, 0.3, 4.0, 1]]])
        assert result == [[1.0, [0.0, 0.0, 0.3, 0.3, 4.0, 1]]]

    def test_multiple_layers(self):
        result = _to_complex([
            [1.0, 0.0],
            [3.5, 0.001],
            [1.0, 0.0],
        ])
        assert result == [
            complex(1.0, 0.0),
            complex(3.5, 0.001),
            complex(1.0, 0.0),
        ]

    def test_plain_numbers_passthrough(self):
        result = _to_complex([1.0, 1.5, 1.0])
        assert result == [1.0, 1.5, 1.0]
