"""Virtual size and fee estimation for a transaction being built.

Underestimating size is the failure mode that matters: a transaction that pays
for fewer virtual bytes than it actually occupies bids below the fee rate it
meant to, and can sit in the mempool for a day. Every number here is therefore
an upper bound - signatures are counted at their maximum encoded length, and
virtual size is rounded up, never down.

Weight units (BIP 141) are the primitive: witness bytes count once, every other
byte counts four times, and virtual size is the weight divided by four. Working
in weight keeps the arithmetic integral until that last division, which is why
the selection strategies price a whole transaction here and round it once.

A part of a transaction says what it costs in one of two ways. A
:class:`ScriptType` names the script kind, which fixes the whole weight of an
input or an output of it, witness data included. A size hint is a size in
virtual bytes - the unlocking or locking script on its own - and is read as a
legacy shape of that size: no witness data, so all of it scaled. The two meet
in :func:`estimate_shaped_weight`, which is what lets one transaction mix them
and still be rounded once.

The input and output counts are assumed to fit in a single-byte varint, which
holds up to 252 of each.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from utxo_select._validate import _check_int

__all__ = [
    "INPUT_OVERHEAD_VSIZE",
    "OUTPUT_OVERHEAD_VSIZE",
    "WITNESS_SCALE_FACTOR",
    "ScriptType",
    "estimate_fee",
    "estimate_shaped_weight",
    "estimate_vsize",
    "estimate_weight",
    "input_weight",
    "output_weight",
    "weight_to_vsize",
]

WITNESS_SCALE_FACTOR = 4

# A fee rate is quoted per this many virtual bytes, as on SelectionRequest.
_FEE_RATE_SCALE = 1000

# version 4 + input count 1 + output count 1 + locktime 4, none of it witness.
_OVERHEAD_WEIGHT = 40

# Marker and flag, carried once by a transaction with any witness input.
_MARKER_WEIGHT = 2

# The empty witness stack a legacy input still carries in a segwit transaction.
_EMPTY_WITNESS_WEIGHT = 1

# outpoint 36 + sequence 4 + a one-byte varint for the unlocking script.
INPUT_OVERHEAD_VSIZE = 41

# amount 8 + a one-byte varint for the locking script.
OUTPUT_OVERHEAD_VSIZE = 9


class ScriptType(Enum):
    """A script kind, which fixes what an input and an output of it cost.

    P2PKH is legacy pay-to-pubkey-hash, P2WPKH is the version 0 witness program
    of the same, and P2TR is a version 1 Taproot output spent through the key
    path. Script path spends depend on the script revealed and are not modelled
    here.
    """

    P2PKH = "p2pkh"
    P2WPKH = "p2wpkh"
    P2TR = "p2tr"

    @property
    def is_segwit(self) -> bool:
        return self is not ScriptType.P2PKH

    @property
    def input_weight(self) -> int:
        """Weight of spending one output of this type, witness included."""
        return _INPUT_WEIGHT[self]

    @property
    def output_weight(self) -> int:
        """Weight of one output of this type, amount and script length included."""
        return _OUTPUT_WEIGHT[self]

    @property
    def input_vsize(self) -> int:
        """Virtual size of one input, rounded up.

        Rounding each input on its own overstates a transaction slightly, so
        this is a safe marginal cost to compare candidates with, not a term to
        sum into a total. Use :func:`estimate_vsize` for the total.
        """
        return weight_to_vsize(self.input_weight)

    @property
    def output_vsize(self) -> int:
        """Virtual size of one output of this type."""
        return weight_to_vsize(self.output_weight)


# Non-witness part of an input is outpoint 36 + scriptSig with its length
# varint + sequence 4; it is scaled, while witness bytes are counted once.
_INPUT_WEIGHT: dict[ScriptType, int] = {
    # scriptSig 1 + 72 signature + 1 + 33 pubkey, so 148 bytes in total.
    ScriptType.P2PKH: 148 * WITNESS_SCALE_FACTOR,
    # 41 bytes with an empty scriptSig; witness 1 + 1 + 72 + 1 + 33.
    ScriptType.P2WPKH: 41 * WITNESS_SCALE_FACTOR + 108,
    # 41 bytes with an empty scriptSig; witness 1 + 1 + 64 Schnorr signature.
    ScriptType.P2TR: 41 * WITNESS_SCALE_FACTOR + 66,
}

# Amount 8 + script length 1 + scriptPubKey of 25, 22 and 34 bytes. An output
# is never witness data, so all of it is scaled.
_OUTPUT_WEIGHT: dict[ScriptType, int] = {
    ScriptType.P2PKH: 34 * WITNESS_SCALE_FACTOR,
    ScriptType.P2WPKH: 31 * WITNESS_SCALE_FACTOR,
    ScriptType.P2TR: 43 * WITNESS_SCALE_FACTOR,
}


def weight_to_vsize(weight: int) -> int:
    """Virtual size of a weight, rounded up as consensus and relay policy do."""
    return -(-weight // WITNESS_SCALE_FACTOR)


def _as_script_types(
    values: Iterable[ScriptType], name: str
) -> tuple[ScriptType, ...]:
    items = tuple(values)
    for item in items:
        if not isinstance(item, ScriptType):
            raise TypeError(
                f"{name} must contain ScriptType members, got "
                f"{type(item).__name__}"
            )
    return items


def input_weight(script_type: ScriptType | None, script_size: int) -> int:
    """Weight one input adds, from its script type or from its size hint.

    The hint is read only when no script type is given, and then as the
    unlocking script of a legacy input stretched to it.
    """
    if script_type is not None:
        return script_type.input_weight
    return (INPUT_OVERHEAD_VSIZE + script_size) * WITNESS_SCALE_FACTOR


def output_weight(script_type: ScriptType | None, script_size: int) -> int:
    """Weight one output adds, from its script type or from its size hint."""
    if script_type is not None:
        return script_type.output_weight
    return (OUTPUT_OVERHEAD_VSIZE + script_size) * WITNESS_SCALE_FACTOR


def estimate_shaped_weight(
    inputs: Iterable[tuple[ScriptType | None, int]],
    outputs: Iterable[tuple[ScriptType | None, int]],
) -> int:
    """Weight of a transaction whose parts are described either way.

    Every entry is a script type, or ``None`` and the size hint to be read
    instead. A part described by a hint is a legacy shape: it carries no
    witness data of its own, and still pays for the empty witness stack that a
    transaction with any witness input gives it.

    Both sequences may be empty, so a partially built transaction can be
    measured while its inputs are still being chosen; the fixed overhead is
    always counted.
    """
    weight = _OVERHEAD_WEIGHT
    witness = False
    legacy = 0

    for script_type, script_size in inputs:
        weight += input_weight(script_type, script_size)
        if script_type is not None and script_type.is_segwit:
            witness = True
        else:
            legacy += 1

    for script_type, script_size in outputs:
        weight += output_weight(script_type, script_size)

    if witness:
        weight += _MARKER_WEIGHT + legacy * _EMPTY_WITNESS_WEIGHT

    return weight


def estimate_weight(
    inputs: Iterable[ScriptType], outputs: Iterable[ScriptType]
) -> int:
    """Weight of a transaction spending ``inputs`` and creating ``outputs``.

    Both arguments are sequences of script types, one entry per input or
    output. Empty sequences are allowed, so a partially built transaction can
    be measured while inputs are still being chosen; the fixed overhead is
    always counted.
    """
    spent = _as_script_types(inputs, "inputs")
    created = _as_script_types(outputs, "outputs")

    # Every part names its type, so the size hint beside it is never read.
    return estimate_shaped_weight(
        ((script_type, 0) for script_type in spent),
        ((script_type, 0) for script_type in created),
    )


def estimate_vsize(
    inputs: Iterable[ScriptType], outputs: Iterable[ScriptType]
) -> int:
    """Virtual size in vbytes, rounded up as consensus and relay policy do."""
    return weight_to_vsize(estimate_weight(inputs, outputs))


def estimate_fee(vsize: int, fee_rate: int) -> int:
    """Fee for ``vsize`` virtual bytes at ``fee_rate`` per 1000 virtual bytes.

    The result is rounded up: paying one base unit more than asked costs
    nothing worth measuring, while paying one less can drop the transaction
    below the rate a fee estimator quoted.
    """
    _check_int(vsize, "vsize", minimum=0)
    _check_int(fee_rate, "fee_rate", minimum=0)
    return -(-(vsize * fee_rate) // _FEE_RATE_SCALE)
