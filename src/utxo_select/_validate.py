"""Integer validation shared by the models and the size estimates.

Amounts, sizes and fee rates are integers of base units everywhere in this
package, and a bool is not one of them: ``True`` would quietly stand in for one
base unit.
"""

from __future__ import annotations


def _check_int(value: object, name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int of base units, got {type(value).__name__}"
        )
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
