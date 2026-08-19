"""Shared accessors for the machine-month pivot cell contract.

Management-report builders expose pivot cells as ``{"hrs": ..., "out": ...}``.
Keeping this lookup in one place prevents the page and Excel export from silently
choosing different values when a pivot contains both measures.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Optional, Union

PivotMeasure = Literal["hrs", "out"]
PivotValue = Optional[Union[int, float, str]]


def pivot_cell(cell: object, measure: PivotMeasure) -> PivotValue:
    """Return one explicit measure from a ``{hrs, out}`` pivot cell.

    Missing and malformed cells deliberately return ``None``.  In particular,
    output never falls back to hours: that would turn a two-measure grid into an
    apparently valid but incorrect output-only grid.
    """
    if not isinstance(cell, Mapping):
        return None
    return cell.get(measure)