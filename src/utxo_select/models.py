"""Immutable models describing what can be spent and what is being paid.

Every amount and size in this module is an integer of base units (satoshis for
Bitcoin-like chains). Floats are rejected at construction time so that rounding
error can never reach a transaction.

An output can say what it costs in one of two ways. ``script_type`` names the
script kind, and a selection then prices it by weight, so the witness discount
and the marker and flag a segwit transaction carries reach the fee.
``script_size`` is the older hint: a size in virtual bytes, read as a legacy
shape of that size. The hint is what a caller gets without asking, and on a
legacy transaction the two paths bill the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from utxo_select._validate import _check_int
from utxo_select.sizes import ScriptType

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


def _check_script_type(value: object, name: str) -> None:
    if value is not None and not isinstance(value, ScriptType):
        raise TypeError(
            f"{name} must be a ScriptType or None, got {type(value).__name__}"
        )


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

    ``script_type`` is the script kind this output is locked to; when it is
    given, the input spending it is priced by weight and ``script_size`` is not
    read. ``script_size`` is the hint used otherwise: the virtual size of the
    unlocking data the input will carry.
    """

    txid: str
    vout: int
    value: int
    confirmations: int = 0
    script_size: int = DEFAULT_INPUT_SCRIPT_SIZE
    script_type: ScriptType | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.txid, str) or not self.txid:
            raise ValueError("txid must be a non-empty string")
        _check_int(self.vout, "vout", minimum=0)
        _check_int(self.value, "value", minimum=1)
        _check_int(self.confirmations, "confirmations", minimum=0)
        _check_int(self.script_size, "script_size", minimum=1)
        _check_script_type(self.script_type, "script_type")

    @property
    def outpoint(self) -> str:
        return f"{self.txid}:{self.vout}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmations > 0


@dataclass(frozen=True, slots=True)
class Target:
    """An amount that must be paid, and the shape of the output paying it.

    ``script_type`` prices the output by weight when it is given; otherwise the
    ``script_size`` hint is read as the locking script of a legacy output.
    """

    value: int
    script_size: int = DEFAULT_OUTPUT_SCRIPT_SIZE
    script_type: ScriptType | None = None

    def __post_init__(self) -> None:
        _check_int(self.value, "value", minimum=1)
        _check_int(self.script_size, "script_size", minimum=1)
        _check_script_type(self.script_type, "script_type")


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    """Everything a selection algorithm needs besides the candidate outputs.

    ``fee_rate`` is given per 1000 virtual bytes so that rates finer than one
    unit per vbyte stay expressible without leaving integer arithmetic.
    ``change_script_type`` describes the change output the way a target
    describes its own, and when it is given it also fixes what spending that
    change output later will cost.
    """

    targets: tuple[Target, ...]
    fee_rate: int
    dust_threshold: int = 546
    change_policy: ChangePolicy = ChangePolicy.ALLOW_CHANGE
    change_script_size: int = DEFAULT_OUTPUT_SCRIPT_SIZE
    change_script_type: ScriptType | None = None

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
        _check_script_type(self.change_script_type, "change_script_type")
        if not isinstance(self.change_policy, ChangePolicy):
            raise TypeError(
                f"change_policy must be a ChangePolicy, got "
                f"{type(self.change_policy).__name__}"
            )

    @property
    def total_target_value(self) -> int:
        return sum(target.value for target in self.targets)
