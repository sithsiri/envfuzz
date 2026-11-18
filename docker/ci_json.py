"""Strict JSON loading shared by the CI and release scripts in this directory."""

from __future__ import annotations

import json
import math
from pathlib import Path


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite number {token}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate keys {duplicates}")
    return dict(pairs)


def load_json(path: Path) -> object:
    """Parse JSON, rejecting NaN, Infinity and duplicate object keys."""
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def is_number(value: object) -> bool:
    """True for a finite int or float that is not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_count(value: object) -> bool:
    """True for a nonnegative int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
