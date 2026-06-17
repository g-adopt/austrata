"""Shared coercion helpers for normalising service property values.

The GA services emit blanks, literal ``"None"``/``"null"``/``"nan"`` strings,
and numbers-as-strings interchangeably. These two helpers give every mapper one
consistent null/parse policy so the domain value objects stay clean.
"""
from __future__ import annotations

from typing import Optional

#: Sentinel strings the services emit that should collapse to ``None``.
_NULL_TOKENS = {"", "none", "null", "nan"}


def to_float(value: object) -> Optional[float]:
    """Coerce a service property to ``float``; blanks/sentinels/garbage -> ``None``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.lower() in _NULL_TOKENS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_str(value: object) -> Optional[str]:
    """Coerce a service property to a stripped ``str``; blanks/sentinels -> ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _NULL_TOKENS:
        return None
    return text
