"""Properties that hold for any wallet, any request and any policy.

The cases here are generated rather than written out: a seeded wallet, a target
somewhere around what that wallet could pay, and a policy on top. One seed is
one case, so a failure names the scenario to replay rather than a shape of
input nobody thought of.

Every input and output in a generated case says what it costs one of the two
ways the models allow - a script type, priced by weight, or a size hint read as
a legacy shape of that size - and the two mix freely within one transaction.
The properties below therefore hold across both paths and any blend of them,
rather than only on the legacy shapes a hint can describe.

The properties are what a caller relies on without checking. A selection
balances and covers the targets plus the fee it reports; it pays for the size
it reports; it never returns change too small to relay; and it never gives more
to the fee than dropping the change output can justify. FORBID_CHANGE is the
deliberate exception to the last one - there the whole remainder becomes fee,
however large, which is the point of that policy.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pytest

from utxo_select import (
    DEFAULT_INPUT_SCRIPT_SIZE,
    ChangePolicy,
    FailureReason,
    InputPreference,
    ScriptType,
    Selection,
    SelectionFailure,
    SelectionPolicy,
    SelectionRequest,
    Target,
    Utxo,
    estimate_fee,
    select_branch_and_bound,
    select_largest_first,
)

# The size model the strategies bill for, restated so that a selection is
# measured against something other than the code that produced it.
WITNESS_SCALE_FACTOR = 4
TX_OVERHEAD_WEIGHT = 40
MARKER_WEIGHT = 2
EMPTY_WITNESS_WEIGHT = 1
INPUT_OVERHEAD_VSIZE = 41
OUTPUT_OVERHEAD_VSIZE = 9

# A shape is a script type, or None and the size hint read in its place. Where
# a type is given, the hint beside it is a different size on purpose: the type
# is what decides.
INPUT_SHAPES = (
    (None, 72),
    (None, 107),
    (None, 148),
    (ScriptType.P2PKH, 107),
    (ScriptType.P2WPKH, 148),
    (ScriptType.P2TR, 72),
)
OUTPUT_SHAPES = (
    (None, 22),
    (None, 25),
    (None, 34),
    (None, 43),
    (ScriptType.P2PKH, 25),
    (ScriptType.P2WPKH, 22),
    (ScriptType.P2TR, 43),
)
FEE_RATES = (0, 250, 1_000, 3_000, 12_000, 40_000)
DUST_THRESHOLDS = (0, 294, 546, 1_000)
CONFIRMATIONS = (0, 0, 1, 3, 6, 100)
WALLET_MAGNITUDES = (300, 5_000, 100_000, 2_000_000)
MIN_CONFIRMATIONS = (0, 0, 1, 6)
CONSOLIDATION_FEE_RATES = (0, 2_000, 10_000)

SEEDS = tuple(range(60))
STRATEGIES = (select_largest_first, select_branch_and_bound)

seeds = pytest.mark.parametrize("seed", SEEDS)
strategies = pytest.mark.parametrize(
    "strategy", STRATEGIES, ids=[strategy.__name__ for strategy in STRATEGIES]
)


@dataclass(frozen=True)
class Scenario:
    utxos: tuple[Utxo, ...]
    request: SelectionRequest
    policy: SelectionPolicy


def weight_to_vsize(weight):
    return -(-weight // WITNESS_SCALE_FACTOR)


def input_weight(script_type, script_size):
    if script_type is not None:
        return script_type.input_weight
    return (INPUT_OVERHEAD_VSIZE + script_size) * WITNESS_SCALE_FACTOR


def output_weight(script_type, script_size):
    if script_type is not None:
        return script_type.output_weight
    return (OUTPUT_OVERHEAD_VSIZE + script_size) * WITNESS_SCALE_FACTOR


def _split(rng, total, parts):
    values = []
    left = total
    for remaining in range(parts - 1, 0, -1):
        take = rng.randint(1, left - remaining)
        values.append(take)
        left -= take
    values.append(left)
    return values


def _wallet(rng):
    utxos = []
    for index in range(rng.randint(1, 10)):
        script_type, script_size = rng.choice(INPUT_SHAPES)
        utxos.append(
            Utxo(
                txid=f"{index:064x}",
                vout=rng.randrange(4),
                value=rng.randint(1, rng.choice(WALLET_MAGNITUDES)),
                confirmations=rng.choice(CONFIRMATIONS),
                script_size=script_size,
                script_type=script_type,
            )
        )
    return tuple(utxos)


def _targets(rng, pool_value):
    wanted = rng.randint(1, pool_value * 13 // 10 + 1)
    count = rng.randint(1, min(3, wanted))
    targets = []
    for value in _split(rng, wanted, count):
        script_type, script_size = rng.choice(OUTPUT_SHAPES)
        targets.append(
            Target(
                value=value,
                script_size=script_size,
                script_type=script_type,
            )
        )
    return tuple(targets)


def scenario(seed):
    rng = random.Random(seed)
    utxos = _wallet(rng)
    targets = _targets(rng, sum(utxo.value for utxo in utxos))
    change_type, change_size = rng.choice(OUTPUT_SHAPES)
    request = SelectionRequest(
        targets=targets,
        fee_rate=rng.choice(FEE_RATES),
        dust_threshold=rng.choice(DUST_THRESHOLDS),
        change_policy=rng.choice(tuple(ChangePolicy)),
        change_script_size=change_size,
        change_script_type=change_type,
    )
    policy = SelectionPolicy(
        min_confirmations=rng.choice(MIN_CONFIRMATIONS),
        input_preference=rng.choice(tuple(InputPreference)),
        consolidation_fee_rate=rng.choice(CONSOLIDATION_FEE_RATES),
    )
    return Scenario(utxos=utxos, request=request, policy=policy)


def run(strategy, case, utxos=None):
    candidates = case.utxos if utxos is None else utxos
    return strategy(candidates, case.request, policy=case.policy)


def effective_value(utxo, fee_rate):
    vsize = weight_to_vsize(input_weight(utxo.script_type, utxo.script_size))
    return utxo.value - estimate_fee(vsize, fee_rate)


def expected_vsize(result, request):
    """Weight of the whole transaction, divided once at the end."""
    weight = TX_OVERHEAD_WEIGHT
    witness = False
    legacy = 0

    for utxo in result.inputs:
        weight += input_weight(utxo.script_type, utxo.script_size)
        if utxo.script_type is not None and utxo.script_type.is_segwit:
            witness = True
        else:
            legacy += 1

    for target in request.targets:
        weight += output_weight(target.script_type, target.script_size)
    if result.has_change:
        weight += output_weight(
            request.change_script_type, request.change_script_size
        )

    if witness:
        weight += MARKER_WEIGHT + legacy * EMPTY_WITNESS_WEIGHT

    return weight_to_vsize(weight)


def wasted_fee_bound(result, request):
    """How far past the fee its size implies a changeless selection may go.

    Dropping the change output is worth its fee now plus the fee of spending it
    later, and a remainder under the dust threshold is not worth returning at
    all. The last term is rounding slack: while the inputs are still being
    chosen both strategies price each of them on its own, and a weight that is
    not a whole number of virtual bytes is rounded up there as well as in the
    total.
    """
    change_output_vsize = weight_to_vsize(
        output_weight(request.change_script_type, request.change_script_size)
    )
    change_spend_vsize = weight_to_vsize(
        input_weight(request.change_script_type, DEFAULT_INPUT_SCRIPT_SIZE)
    )
    rounded_apart = len(result.inputs) + 1
    return (
        max(request.dust_threshold, 1)
        + estimate_fee(change_output_vsize, request.fee_rate)
        + estimate_fee(change_spend_vsize, request.fee_rate)
        + rounded_apart * (estimate_fee(1, request.fee_rate) + 1)
    )


def test_the_generated_wallets_both_pay_and_fail():
    paid = sum(
        isinstance(run(select_largest_first, scenario(seed)), Selection)
        for seed in SEEDS
    )
    assert 5 <= paid <= len(SEEDS) - 3


def test_the_generated_cases_describe_their_shapes_both_ways():
    typed = 0
    hinted = 0
    for seed in SEEDS:
        case = scenario(seed)
        for utxo in case.utxos:
            if utxo.script_type is None:
                hinted += 1
            else:
                typed += 1
        for target in case.request.targets:
            if target.script_type is None:
                hinted += 1
            else:
                typed += 1
    assert typed > 0
    assert hinted > 0


@seeds
@strategies
def test_a_selection_covers_the_targets_and_its_fee(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, Selection):
        return

    total_input = sum(utxo.value for utxo in result.inputs)
    target_value = case.request.total_target_value
    assert result.change >= 0
    assert result.fee >= 0
    assert total_input >= target_value + result.fee
    assert total_input == target_value + result.change + result.fee


@seeds
@strategies
def test_change_is_never_left_below_the_dust_threshold(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, Selection):
        return

    minimum_change = max(case.request.dust_threshold, 1)
    assert result.change == 0 or result.change >= minimum_change


@seeds
@strategies
def test_the_reported_size_matches_the_inputs_and_outputs(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, Selection):
        return

    assert result.vsize == expected_vsize(result, case.request)


@seeds
@strategies
def test_the_fee_pays_for_the_size_it_reports(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, Selection):
        return

    assert result.fee >= estimate_fee(result.vsize, case.request.fee_rate)


@seeds
@strategies
def test_the_fee_never_overpays_beyond_the_cost_of_change(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, Selection):
        return
    if case.request.change_policy is ChangePolicy.FORBID_CHANGE:
        return

    required = estimate_fee(result.vsize, case.request.fee_rate)
    if result.has_change:
        assert result.fee == required
    else:
        assert result.fee - required <= wasted_fee_bound(result, case.request)


@seeds
@strategies
def test_only_candidates_worth_spending_are_spent(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, Selection):
        return

    outpoints = [utxo.outpoint for utxo in result.inputs]
    assert len(set(outpoints)) == len(outpoints)
    for utxo in result.inputs:
        assert utxo in case.utxos
        assert utxo.confirmations >= case.policy.min_confirmations
        assert effective_value(utxo, case.request.fee_rate) > 0


@seeds
@strategies
def test_the_change_policy_is_honoured(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, Selection):
        return

    if case.request.change_policy is ChangePolicy.FORBID_CHANGE:
        assert not result.has_change
    if case.request.change_policy is ChangePolicy.REQUIRE_CHANGE:
        assert result.has_change


@seeds
@strategies
def test_a_failure_names_a_gap_that_is_really_there(seed, strategy):
    case = scenario(seed)
    result = run(strategy, case)
    if not isinstance(result, SelectionFailure):
        return

    assert result.required > result.available
    assert result.shortfall > 0
    assert result.candidate_count == len(case.utxos)
    assert result.eligible_count <= result.candidate_count
    assert result.spendable_count <= result.candidate_count
    assert result.withheld_count >= 0
    assert result.dust_count >= 0
    assert result.target_value == case.request.total_target_value
    assert result.reason.value in str(result)
    if result.reason is FailureReason.INSUFFICIENT_FUNDS:
        assert result.required == case.request.total_target_value


@seeds
@strategies
def test_the_result_does_not_depend_on_the_order_of_the_candidates(
    seed, strategy
):
    case = scenario(seed)
    shuffled = list(case.utxos)
    random.Random(seed + len(SEEDS)).shuffle(shuffled)

    assert run(strategy, case, shuffled) == run(strategy, case)


@seeds
def test_branch_and_bound_fails_only_where_largest_first_does(seed):
    case = scenario(seed)
    baseline = run(select_largest_first, case)
    searched = run(select_branch_and_bound, case)

    if isinstance(searched, SelectionFailure):
        assert baseline == searched
    else:
        assert isinstance(baseline, Selection)
