"""Coin selection strategies.

Largest-first is the baseline: order the candidates by value and take them
until the targets and the fee are covered. It is rarely the cheapest choice -
it spends large outputs and leaves a trail of small ones behind - but it is
predictable, and predictability is what a baseline is for.

Branch-and-bound looks for a subset that pays the targets and the fee with no
remainder worth returning, so the transaction carries no change output at all.
A changeless spend saves the change output's fee now and the fee of spending
that output later, and those two together bound how much overpayment is still
an improvement. When no such subset turns up within the search budget the
caller gets the largest-first result instead.

Sizes here come from the ``script_size`` hints carried by the models rather
than from :class:`~utxo_select.sizes.ScriptType`: a transaction costs a fixed
overhead, plus 41 virtual bytes and the unlocking script for every input, plus
9 virtual bytes and the locking script for every output. Script sizes are read
as virtual bytes, so a hint for a witness script should already be discounted.

A selection either covers the targets and the fee in full, or it comes back as
a :class:`SelectionFailure`. The identity inputs == targets + change + fee is
checked before a :class:`Selection` can exist, so an underpaying result cannot
be constructed by accident. A failure is a value rather than an exception, and
it separates the outcomes a caller would answer differently: a wallet that is
too small for the targets, one that covers the targets but not the fee on top,
and one holding nothing but outputs that cost more to spend than they hold.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from utxo_select.models import (
    DEFAULT_INPUT_SCRIPT_SIZE,
    ChangePolicy,
    SelectionRequest,
    Target,
    Utxo,
    _check_int,
)
from utxo_select.sizes import estimate_fee

__all__ = [
    "FailureReason",
    "Selection",
    "SelectionFailure",
    "select_branch_and_bound",
    "select_largest_first",
]

# version 4 + locktime 4 + a one-byte varint for each of the two counts.
_TX_OVERHEAD_VSIZE = 10

# outpoint 36 + sequence 4 + a one-byte varint for the unlocking script.
_INPUT_OVERHEAD_VSIZE = 41

# amount 8 + a one-byte varint for the locking script.
_OUTPUT_OVERHEAD_VSIZE = 9

# Nodes the branch-and-bound search may visit before it gives up.
_DEFAULT_MAX_TRIES = 100_000


class FailureReason(Enum):
    """Why no selection could be made.

    INSUFFICIENT_FUNDS means the candidates do not hold the targets even
    before a fee is counted; no fee rate makes that work.
    INSUFFICIENT_AFTER_FEES means they hold the targets but not the fee the
    transaction spending them would owe on top, so a lower rate or fewer,
    larger inputs can still close the gap. DUST_ONLY means every candidate
    costs at least as much to spend as it holds, so at this rate the wallet
    cannot pay anything at all. CHANGE_BELOW_DUST means the candidates can
    pay, but not while also leaving a change output worth relaying, which only
    fails a selection under :attr:`~utxo_select.ChangePolicy.REQUIRE_CHANGE`.
    """

    INSUFFICIENT_FUNDS = "insufficient_funds"
    INSUFFICIENT_AFTER_FEES = "insufficient_after_fees"
    DUST_ONLY = "dust_only"
    CHANGE_BELOW_DUST = "change_below_dust"


@dataclass(frozen=True, slots=True)
class Selection:
    """Inputs that cover the targets, with the fee and change they imply.

    ``change`` is zero when no change output is created, in which case the
    remainder has been given to the fee. ``vsize`` is the estimated virtual
    size of the transaction these inputs and outputs form, change included.
    """

    inputs: tuple[Utxo, ...]
    targets: tuple[Target, ...]
    change: int
    fee: int
    vsize: int

    def __post_init__(self) -> None:
        if not self.inputs:
            raise ValueError("a selection must spend at least one input")
        if self.change < 0:
            raise ValueError(f"change must be >= 0, got {self.change}")
        if self.fee < 0:
            raise ValueError(f"fee must be >= 0, got {self.fee}")
        if self.total_input != self.total_output + self.fee:
            raise ValueError(
                f"selection does not balance: {self.total_input} in, "
                f"{self.total_output} out, {self.fee} fee"
            )

    @property
    def total_input(self) -> int:
        return sum(utxo.value for utxo in self.inputs)

    @property
    def total_output(self) -> int:
        return sum(target.value for target in self.targets) + self.change

    @property
    def has_change(self) -> bool:
        return self.change > 0


@dataclass(frozen=True, slots=True)
class SelectionFailure:
    """Why the candidates could not pay, with the numbers behind it.

    ``available`` is what the candidates hold together and ``required`` what
    they would have had to hold for the attempt to succeed: the targets alone
    for INSUFFICIENT_FUNDS, the targets and the fee for INSUFFICIENT_AFTER_FEES
    and DUST_ONLY, and those plus the smallest change output worth creating for
    CHANGE_BELOW_DUST. ``fee`` is what a transaction spending every candidate
    would owe, so ``required - fee`` is the part of the gap the fee rate is not
    responsible for. ``spendable_count`` counts the candidates worth more than
    the fee of spending them; the rest are dust at this rate.
    """

    reason: FailureReason
    available: int
    required: int
    target_value: int = 0
    fee: int = 0
    candidate_count: int = 0
    spendable_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.reason, FailureReason):
            raise TypeError(
                f"reason must be a FailureReason, got "
                f"{type(self.reason).__name__}"
            )
        _check_int(self.available, "available", minimum=0)
        _check_int(self.required, "required", minimum=0)
        _check_int(self.target_value, "target_value", minimum=0)
        _check_int(self.fee, "fee", minimum=0)
        _check_int(self.candidate_count, "candidate_count", minimum=0)
        _check_int(self.spendable_count, "spendable_count", minimum=0)
        if self.spendable_count > self.candidate_count:
            raise ValueError(
                f"spendable_count {self.spendable_count} exceeds "
                f"candidate_count {self.candidate_count}"
            )

    @property
    def shortfall(self) -> int:
        return max(self.required - self.available, 0)

    @property
    def dust_count(self) -> int:
        """Candidates that cost at least as much to spend as they hold."""
        return self.candidate_count - self.spendable_count

    def __str__(self) -> str:
        if self.reason is FailureReason.INSUFFICIENT_FUNDS:
            return (
                f"{self.reason.value}: {self.available} available against "
                f"{self.target_value} in targets, short by {self.shortfall} "
                f"before the {self.fee} fee"
            )
        if self.reason is FailureReason.INSUFFICIENT_AFTER_FEES:
            return (
                f"{self.reason.value}: {self.available} available covers "
                f"{self.target_value} in targets but not the {self.fee} fee "
                f"on top, short by {self.shortfall}"
            )
        if self.reason is FailureReason.DUST_ONLY:
            return (
                f"{self.reason.value}: all {self.candidate_count} candidates "
                f"cost more to spend than they hold at this fee rate, "
                f"{self.available} available of {self.required} required"
            )
        return (
            f"{self.reason.value}: {self.available} available pays "
            f"{self.target_value} and the {self.fee} fee but leaves no change "
            f"worth relaying, short by {self.shortfall}"
        )


def _input_vsize(utxo: Utxo) -> int:
    return _INPUT_OVERHEAD_VSIZE + utxo.script_size


def _output_vsize(script_size: int) -> int:
    return _OUTPUT_OVERHEAD_VSIZE + script_size


def _base_vsize(request: SelectionRequest) -> int:
    return _TX_OVERHEAD_VSIZE + sum(
        _output_vsize(target.script_size) for target in request.targets
    )


def _effective_value(utxo: Utxo, fee_rate: int) -> int:
    """What an output is worth once the fee for spending it is taken off."""
    return utxo.value - estimate_fee(_input_vsize(utxo), fee_rate)


def _ordered_candidates(utxos: Iterable[Utxo]) -> tuple[Utxo, ...]:
    candidates = tuple(utxos)
    seen: set[str] = set()
    for utxo in candidates:
        if not isinstance(utxo, Utxo):
            raise TypeError(
                f"utxos must contain Utxo instances, got {type(utxo).__name__}"
            )
        if utxo.outpoint in seen:
            raise ValueError(f"duplicate candidate outpoint: {utxo.outpoint}")
        seen.add(utxo.outpoint)
    return tuple(sorted(candidates, key=lambda u: (-u.value, u.outpoint)))


def _explain_failure(
    candidates: Sequence[Utxo], request: SelectionRequest
) -> SelectionFailure:
    """Say which way the candidates fell short, spending all of them.

    The three shortfalls are told apart by what the candidates hold against
    the targets alone and against the targets plus the fee; a wallet whose
    outputs are all dust is reported as such first, because there the fee rate
    rather than the amount is what makes it unspendable.
    """
    target_value = request.total_target_value
    available = sum(utxo.value for utxo in candidates)
    vsize = _base_vsize(request) + sum(
        _input_vsize(utxo) for utxo in candidates
    )
    fee = estimate_fee(vsize, request.fee_rate)
    spendable = sum(
        1
        for utxo in candidates
        if _effective_value(utxo, request.fee_rate) > 0
    )

    def failure(
        reason: FailureReason, required: int, attempt_fee: int = fee
    ) -> SelectionFailure:
        return SelectionFailure(
            reason=reason,
            available=available,
            required=required,
            target_value=target_value,
            fee=attempt_fee,
            candidate_count=len(candidates),
            spendable_count=spendable,
        )

    if candidates and spendable == 0:
        return failure(FailureReason.DUST_ONLY, target_value + fee)
    if available < target_value:
        return failure(FailureReason.INSUFFICIENT_FUNDS, target_value)
    if available < target_value + fee:
        return failure(FailureReason.INSUFFICIENT_AFTER_FEES, target_value + fee)

    fee_with_change = estimate_fee(
        vsize + _output_vsize(request.change_script_size), request.fee_rate
    )
    minimum_change = max(request.dust_threshold, 1)
    return failure(
        FailureReason.CHANGE_BELOW_DUST,
        target_value + fee_with_change + minimum_change,
        fee_with_change,
    )


def select_largest_first(
    utxos: Iterable[Utxo], request: SelectionRequest
) -> Selection | SelectionFailure:
    """Select inputs by descending value until the request is covered.

    Candidates are ordered by value, then by outpoint so that equal values do
    not make the result depend on the order they were passed in. Each further
    input pays for itself: the fee is re-estimated over the transaction as it
    then stands, and the search stops at the first prefix that covers the
    targets and that fee.

    Under ALLOW_CHANGE a remainder that reaches the dust threshold becomes a
    change output and anything smaller is given to the fee. FORBID_CHANGE gives
    the whole remainder to the fee, however large it is, so it should be used
    with candidates that are close to the target. REQUIRE_CHANGE keeps adding
    inputs until a change output above the threshold is possible.

    When nothing covers the request the result is a :class:`SelectionFailure`
    naming which shortfall it was, with the amounts it was measured against.

    Duplicate outpoints raise :exc:`ValueError`: spending one twice is a caller
    bug, not a selection that failed.
    """
    candidates = _ordered_candidates(utxos)

    target_value = request.total_target_value
    change_vsize = _output_vsize(request.change_script_size)
    minimum_change = max(request.dust_threshold, 1)
    wants_change = request.change_policy is not ChangePolicy.FORBID_CHANGE

    vsize = _base_vsize(request)
    total_input = 0
    chosen: list[Utxo] = []

    for utxo in candidates:
        chosen.append(utxo)
        total_input += utxo.value
        vsize += _input_vsize(utxo)

        fee = estimate_fee(vsize, request.fee_rate)
        if total_input - target_value - fee < 0:
            continue

        if wants_change:
            fee_with_change = estimate_fee(
                vsize + change_vsize, request.fee_rate
            )
            change = total_input - target_value - fee_with_change
            if change >= minimum_change:
                return Selection(
                    inputs=tuple(chosen),
                    targets=request.targets,
                    change=change,
                    fee=fee_with_change,
                    vsize=vsize + change_vsize,
                )
            if request.change_policy is ChangePolicy.REQUIRE_CHANGE:
                continue

        return Selection(
            inputs=tuple(chosen),
            targets=request.targets,
            change=0,
            fee=total_input - target_value,
            vsize=vsize,
        )

    return _explain_failure(candidates, request)


def select_branch_and_bound(
    utxos: Iterable[Utxo],
    request: SelectionRequest,
    *,
    max_tries: int = _DEFAULT_MAX_TRIES,
    change_spend_script_size: int = DEFAULT_INPUT_SCRIPT_SIZE,
) -> Selection | SelectionFailure:
    """Search for a subset that pays the request without a change output.

    Candidates are compared by effective value - what an output is worth once
    the fee for spending it has been taken off - so an output that costs more
    to spend than it holds is never considered. A subset is a solution when its
    effective value covers the targets and the fee of a transaction without a
    change output, and overshoots by no more than the cost of change: the fee
    of the change output now plus the fee of spending it later, estimated with
    ``change_spend_script_size`` as the unlocking size of that future input.
    Overshooting by less than that is cheaper than returning the remainder.

    The search is depth-first over candidates in descending effective value,
    visiting at most ``max_tries`` nodes and keeping the solution that
    overshoots least. When it finds none - the usual case for a wallet whose
    outputs do not happen to add up - :func:`select_largest_first` answers
    instead, so the fallback is a normal selection, or the same explained
    failure the baseline would have given.

    REQUIRE_CHANGE is delegated to the baseline unchanged: a policy that
    demands a change output has nothing to gain from a changeless search.
    """
    _check_int(max_tries, "max_tries", minimum=0)
    _check_int(change_spend_script_size, "change_spend_script_size", minimum=1)

    candidates = _ordered_candidates(utxos)
    if request.change_policy is ChangePolicy.REQUIRE_CHANGE:
        return select_largest_first(candidates, request)

    base_vsize = _base_vsize(request)
    target_value = request.total_target_value
    selection_target = target_value + estimate_fee(base_vsize, request.fee_rate)
    cost_of_change = estimate_fee(
        _output_vsize(request.change_script_size), request.fee_rate
    ) + estimate_fee(
        _INPUT_OVERHEAD_VSIZE + change_spend_script_size, request.fee_rate
    )

    pool: list[tuple[int, Utxo]] = []
    for utxo in candidates:
        effective = _effective_value(utxo, request.fee_rate)
        if effective > 0:
            pool.append((effective, utxo))
    pool.sort(key=lambda item: (-item[0], item[1].outpoint))

    chosen = _search_changeless(
        pool, selection_target, cost_of_change, max_tries
    )
    if chosen is None:
        return select_largest_first(candidates, request)

    total_input = sum(utxo.value for utxo in chosen)
    vsize = base_vsize + sum(_input_vsize(utxo) for utxo in chosen)
    return Selection(
        inputs=chosen,
        targets=request.targets,
        change=0,
        fee=total_input - target_value,
        vsize=vsize,
    )


def _search_changeless(
    pool: Sequence[tuple[int, Utxo]],
    selection_target: int,
    cost_of_change: int,
    max_tries: int,
) -> tuple[Utxo, ...] | None:
    values = [effective for effective, _ in pool]
    available = sum(values)
    if available < selection_target:
        return None

    selected: list[bool] = []
    current = 0
    best: list[bool] | None = None
    best_excess = cost_of_change + 1

    for _ in range(max_tries):
        backtrack = False
        if current + available < selection_target:
            backtrack = True
        elif current > selection_target + cost_of_change:
            backtrack = True
        elif current >= selection_target:
            excess = current - selection_target
            if excess < best_excess:
                best_excess = excess
                best = list(selected)
                if excess == 0:
                    break
            backtrack = True

        if backtrack:
            while selected and not selected[-1]:
                selected.pop()
                available += values[len(selected)]
            if not selected:
                break
            selected[-1] = False
            current -= values[len(selected) - 1]
        else:
            index = len(selected)
            available -= values[index]
            # Omitting an output and then including its equal is the same
            # subset by value, so only the first ordering is explored.
            if (
                selected
                and not selected[-1]
                and values[index] == values[index - 1]
            ):
                selected.append(False)
            else:
                selected.append(True)
                current += values[index]

    if best is None:
        return None
    return tuple(
        pool[index][1] for index, included in enumerate(best) if included
    )
