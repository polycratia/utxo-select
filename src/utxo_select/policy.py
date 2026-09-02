"""How a selection chooses among candidates that could all pay the same bill.

Two selections can pay the same targets at the same fee rate and still differ
in ways a wallet cares about: whether an output that is not yet buried deeply
enough was spent, how many of the owner's outputs were tied together in one
transaction for anyone reading the chain, and whether a cheap block was used
to tidy up a wallet full of small outputs.

Those choices are a policy, and the policy is a parameter of the existing
strategies rather than a strategy of its own: it filters the candidates and
decides the order they are tried in, and the algorithm above it is the same
one either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from utxo_select.models import Utxo, _check_int

__all__ = [
    "DEFAULT_CONSOLIDATION_FEE_RATE",
    "DEFAULT_POLICY",
    "InputPreference",
    "SelectionPolicy",
]

# Two base units per virtual byte, quoted per 1000 as fee rates are: the kind
# of rate at which sweeping small outputs is worth its own fee.
DEFAULT_CONSOLIDATION_FEE_RATE = 2_000


class InputPreference(Enum):
    """Whether a selection would rather spend few outputs or many.

    FEWER_INPUTS takes candidates by descending value, so the targets are
    covered by as few outputs as possible: a smaller transaction, and fewer of
    the owner's outputs tied together for an observer to read. MORE_INPUTS
    reverses that and sweeps small outputs up instead, paying more in fees now
    for a wallet that is cheaper to spend from later. CONSOLIDATE_WHEN_CHEAP
    does the same, but only while the fee rate is at or below
    :attr:`SelectionPolicy.consolidation_fee_rate`, and behaves as
    FEWER_INPUTS above it.
    """

    FEWER_INPUTS = "fewer_inputs"
    MORE_INPUTS = "more_inputs"
    CONSOLIDATE_WHEN_CHEAP = "consolidate_when_cheap"


@dataclass(frozen=True, slots=True)
class SelectionPolicy:
    """Which candidates may be spent, and in what order they are tried.

    ``min_confirmations`` is the depth an output must have reached before it
    may be spent; zero accepts anything, including outputs still in the
    mempool. ``consolidation_fee_rate`` is quoted per 1000 virtual bytes like
    the rate on a request, and is read only by
    :attr:`InputPreference.CONSOLIDATE_WHEN_CHEAP`.
    """

    min_confirmations: int = 0
    input_preference: InputPreference = InputPreference.FEWER_INPUTS
    consolidation_fee_rate: int = DEFAULT_CONSOLIDATION_FEE_RATE

    def __post_init__(self) -> None:
        _check_int(self.min_confirmations, "min_confirmations", minimum=0)
        _check_int(
            self.consolidation_fee_rate, "consolidation_fee_rate", minimum=0
        )
        if not isinstance(self.input_preference, InputPreference):
            raise TypeError(
                f"input_preference must be an InputPreference, got "
                f"{type(self.input_preference).__name__}"
            )

    def accepts(self, utxo: Utxo) -> bool:
        """Whether this output is confirmed deeply enough to be spent."""
        return utxo.confirmations >= self.min_confirmations

    def consolidates_at(self, fee_rate: int) -> bool:
        """Whether small outputs are taken first at this fee rate."""
        if self.input_preference is InputPreference.MORE_INPUTS:
            return True
        if self.input_preference is InputPreference.CONSOLIDATE_WHEN_CHEAP:
            return fee_rate <= self.consolidation_fee_rate
        return False


# Spend anything, largest first: what a caller gets without asking.
DEFAULT_POLICY = SelectionPolicy()
