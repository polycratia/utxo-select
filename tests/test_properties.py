"""Properties that hold for any wallet, any request and any policy.

The cases here are generated rather than written out: a seeded wallet, a target
somewhere around what that wallet could pay, and a policy on top. One seed is
one case, so a failure names the scenario to replay rather than a shape of
input nobody thought of.

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
TX_OVERHEAD_VSIZE = 10
INPUT_OVERHEAD_VSIZE = 41
OUTPUT_OVERHEAD_VSIZE = 9

INPUT_SCRIPT_SIZES = (72, 107, 148)
OUTPUT_SCRIPT_SIZES = (22, 25, 34, 43)
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
    return tuple(
        Utxo(
            txid=f"{index:064x}",
            vout=rng.randrange(4),
            value=rng.randint(1, rng.choice(WALLET_MAGNITUDES)),
            confirmations=rng.choice(CONFIRMATIONS),
            script_size=rng.choice(INPUT_SCRIPT_SIZES),
        )
        for index in range(rng.randint(1, 10))
    )


def _targets(rng, pool_value):
    wanted = rng.randint(1, pool_value * 13 // 10 + 1)
    count = rng.randint(1, min(3, wanted))
    return tuple(
        Target(value=value, script_size=rng.choice(OUTPUT_SCRIPT_SIZES))
        for value in _split(rng, wanted, count)
    )


def scenario(seed):
    rng = random.Random(seed)
    utxos = _wallet(rng)
    request = SelectionRequest(
        targets=_targets(rng, sum(utxo.value for utxo in utxos)),
        fee_rate=rng.choice(FEE_RATES),
        dust_threshold=rng.choice(DUST_THRESHOLDS),
        change_policy=rng.choice(tuple(ChangePolicy)),
        change_script_size=rng.choice(OUTPUT_SCRIPT_SIZES),
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
    return utxo.value - estimate_fee(
        INPUT_OVERHEAD_VSIZE + utxo.script_size, fee_rate
    )


def expected_vsize(result, request):
    vsize = TX_OVERHEAD_VSIZE
    vsize += sum(
        INPUT_OVERHEAD_VSIZE + utxo.script_size for utxo in result.inputs
    )
    vsize += sum(
        OUTPUT_OVERHEAD_VSIZE + target.script_size for target in request.targets
    )
    if result.has_change:
        vsize += OUTPUT_OVERHEAD_VSIZE + request.change_script_size
    return vsize


def wasted_fee_bound(result, request):
    """How far past the fee its size implies a changeless selection may go.

    Dropping the change output is worth its fee now plus the fee of spending it
    later, and a remainder under the dust threshold is not worth returning at
    all. The term per input is the rounding slack of pricing each input on its
    own, which both strategies do while they are still choosing.
    """
    return (
        max(request.dust_threshold, 1)
        + estimate_fee(
            OUTPUT_OVERHEAD_VSIZE + request.change_script_size, request.fee_rate
        )
        + estimate_fee(
            INPUT_OVERHEAD_VSIZE + DEFAULT_INPUT_SCRIPT_SIZE, request.fee_rate
        )
        + len(result.inputs)
    )


def test_the_generated_wallets_both_pay_and_fail():
    paid = sum(
        isinstance(run(select_largest_first, scenario(seed)), Selection)
        for seed in SEEDS
    )
    assert 5 <= paid <= len(SEEDS) - 3


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
