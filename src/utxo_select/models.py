"""Immutable models describing what can be spent and what is being paid.

Every amount and size in this module is an integer of base units (satoshis for
Bitcoin-like chains). Floats are rejected at construction time so that rounding
error can never reach a transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "DEFAULT_INPUT_SCRIPT_SIZE",
    "DEFAULT_OUTPUT_SCRIPT_SIZE",
    "ChangePolicy",
    "SelectionRequest",
    "Target",
    "Utxo",
]

# Unlocking data of a legacy P2PKH input, in virtual bytes.
DEFAULT_INPUT_SCRIPT_SIZE = 107

# Locking script of a legacy P2PKH output, in virtual bytes.
DEFAULT_OUTPUT_SCRIPT_SIZE = 25


def _check_int(value: object, name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int of base units, got {type(value).__name__}"
        )
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")


class ChangePolicy(Enum):
    """What a selection may do with the remainder after targets and fee.

    ALLOW_CHANGE adds a change output when the remainder reaches the dust
    threshold and gives it to the fee otherwise. FORBID_CHANGE never adds a
    change output, so the whole remainder becomes fee. REQUIRE_CHANGE rejects
    any selection that cannot leave a change output above the dust threshold.
    """

    ALLOW_CHANGE = "allow_change"
    FORBID_CHANGE = "forbid_change"
    REQUIRE_CHANGE = "require_change"


@dataclass(frozen=True, slots=True)
class Utxo:
    """A spendable output.

    ``script_size`` is a hint: the virtual size of the unlocking data this
    input will carry, used to estimate the fee the input costs to spend.
    """

    txid: str
    vout: int
    value: int
    confirmations: int = 0
    script_size: int = DEFAULT_INPUT_SCRIPT_SIZE

    def __post_init__(self) -> None:
        if not isinstance(self.txid, str) or not self.txid:
            raise ValueError("txid must be a non-empty string")
        _check_int(self.vout, "vout", minimum=0)
        _check_int(self.value, "value", minimum=1)
        _check_int(self.confirmations, "confirmations", minimum=0)
        _check_int(self.script_size, "script_size", minimum=1)

    @property
    def outpoint(self) -> str:
        return f"{self.txid}:{self.vout}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmations > 0


@dataclass(frozen=True, slots=True)
class Target:
    """An amount that must be paid, and the size of the output paying it."""

    value: int
    script_size: int = DEFAULT_OUTPUT_SCRIPT_SIZE

    def __post_init__(self) -> None:
        _check_int(self.value, "value", minimum=1)
        _check_int(self.script_size, "script_size", minimum=1)


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    """Everything a selection algorithm needs besides the candidate outputs.

    ``fee_rate`` is given per 1000 virtual bytes so that rates finer than one
    unit per vbyte stay expressible without leaving integer arithmetic.
    """

    targets: tuple[Target, ...]
    fee_rate: int
    dust_threshold: int = 546
    change_policy: ChangePolicy = ChangePolicy.ALLOW_CHANGE
    change_script_size: int = DEFAULT_OUTPUT_SCRIPT_SIZE

    def __post_init__(self) -> None:
        targets = tuple(self.targets)
        if not targets:
            raise ValueError("targets must not be empty")
        for target in targets:
            if not isinstance(target, Target):
                raise TypeError(
                    f"targets must contain Target instances, got "
                    f"{type(target).__name__}"
                )
        object.__setattr__(self, "targets", targets)

        _check_int(self.fee_rate, "fee_rate", minimum=0)
        _check_int(self.dust_threshold, "dust_threshold", minimum=0)
        _check_int(self.change_script_size, "change_script_size", minimum=1)
        if not isinstance(self.change_policy, ChangePolicy):
            raise TypeError(
                f"change_policy must be a ChangePolicy, got "
                f"{type(self.change_policy).__name__}"
            )

    @property
    def total_target_value(self) -> int:
        return sum(target.value for target in self.targets)
