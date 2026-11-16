"""Failure modes, and the boundaries a selection turns on.

A failure is a returned value rather than an exception, and the point of the
five reasons is that a caller answers them differently - by funding the wallet,
by lowering the fee rate, by waiting, or by asking for less change. Each case
here is a wallet that can only fail one of those ways, checked against the
numbers reported with the verdict.

The amounts are chosen to land exactly on a boundary: a remainder one base unit
under the dust threshold, an overshoot equal to the cost of change, and the
same overshoot one unit past it. That is where an off-by-one in either strategy
shows up and nowhere else. A caller bug is the one thing that does raise.
"""

import pytest

from utxo_select import (
    ChangePolicy,
    FailureReason,
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

FEE_RATE = 1_000

# One legacy input and one legacy target is 10 + 148 + 34 vbytes, and 226 with
# a legacy change output beside the target.
CHANGELESS_VSIZE = 192
CHANGE_VSIZE = 226

# The change output's own fee, 34 vbytes, plus the 148 to spend it later.
COST_OF_CHANGE = 182

# A legacy input costs this much to spend at 12 base units per vbyte.
DEAR_FEE_RATE = 12_000
INPUT_FEE_WHEN_DEAR = 1_776

STRATEGIES = (select_largest_first, select_branch_and_bound)
strategies = pytest.mark.parametrize(
    "strategy", STRATEGIES, ids=[strategy.__name__ for strategy in STRATEGIES]
)


def utxo(value, *, tag="a", confirmations=6):
    return Utxo(txid=tag * 64, vout=0, value=value, confirmations=confirmations)


def request(
    value=100_000,
    *,
    fee_rate=FEE_RATE,
    dust_threshold=546,
    change_policy=ChangePolicy.ALLOW_CHANGE,
):
    return SelectionRequest(
        targets=(Target(value=value),),
        fee_rate=fee_rate,
        dust_threshold=dust_threshold,
        change_policy=change_policy,
    )


@strategies
def test_an_empty_wallet_is_short_of_the_targets_alone(strategy):
    result = strategy([], request())

    assert isinstance(result, SelectionFailure)
    assert result.reason is FailureReason.INSUFFICIENT_FUNDS
    assert result.available == 0
    assert result.required == 100_000
    assert result.shortfall == 100_000
    assert result.candidate_count == 0
    assert result.eligible_count == 0
    assert result.spendable_count == 0
    assert result.dust_count == 0
    assert result.withheld_count == 0
    # No inputs to pay for, so the fee is the overhead and the one target.
    assert result.fee == 44


@strategies
def test_a_wallet_of_only_dust_blames_the_fee_rate(strategy):
    result = strategy(
        [utxo(900, tag="a"), utxo(1_000, tag="b")],
        request(fee_rate=DEAR_FEE_RATE),
    )

    assert isinstance(result, SelectionFailure)
    assert result.reason is FailureReason.DUST_ONLY
    assert result.available == 0
    assert result.candidate_count == 2
    assert result.spendable_count == 0
    assert result.dust_count == 2
    assert result.required == 100_528
    assert "cost more to spend than they hold" in str(result)


def test_an_output_worth_exactly_its_own_fee_is_dust():
    dust = select_largest_first(
        [utxo(INPUT_FEE_WHEN_DEAR)], request(fee_rate=DEAR_FEE_RATE)
    )
    worth_a_unit = select_largest_first(
        [utxo(INPUT_FEE_WHEN_DEAR + 1)], request(fee_rate=DEAR_FEE_RATE)
    )

    assert isinstance(dust, SelectionFailure)
    assert dust.reason is FailureReason.DUST_ONLY
    assert dust.dust_count == 1

    assert isinstance(worth_a_unit, SelectionFailure)
    assert worth_a_unit.reason is FailureReason.INSUFFICIENT_FUNDS
    assert worth_a_unit.spendable_count == 1
    assert worth_a_unit.dust_count == 0
    assert worth_a_unit.available == INPUT_FEE_WHEN_DEAR + 1


@strategies
def test_funds_missing_before_a_fee_is_counted_at_all(strategy):
    result = strategy([utxo(99_000)], request())

    assert isinstance(result, SelectionFailure)
    assert result.reason is FailureReason.INSUFFICIENT_FUNDS
    assert result.required == result.target_value == 100_000
    assert result.shortfall == 1_000
    assert result.fee == CHANGELESS_VSIZE
    assert "before the 192 fee" in str(result)


@strategies
def test_funds_that_cover_the_targets_but_not_the_fee_on_top(strategy):
    result = strategy([utxo(100_100)], request())

    assert isinstance(result, SelectionFailure)
    assert result.reason is FailureReason.INSUFFICIENT_AFTER_FEES
    assert result.available == 100_100
    assert result.required == 100_192
    assert result.shortfall == 92
    assert result.fee == CHANGELESS_VSIZE
    assert "not the 192 fee on top" in str(result)


def test_change_below_dust_only_fails_the_policy_that_demands_change():
    wallet = [utxo(100_300)]

    allowed = select_largest_first(wallet, request())
    demanded = select_largest_first(
        wallet, request(change_policy=ChangePolicy.REQUIRE_CHANGE)
    )

    assert isinstance(allowed, Selection)
    assert not allowed.has_change

    assert isinstance(demanded, SelectionFailure)
    assert demanded.reason is FailureReason.CHANGE_BELOW_DUST
    assert demanded.available == 100_300
    assert demanded.required == 100_000 + CHANGE_VSIZE + 546
    assert demanded.fee == CHANGE_VSIZE
    assert demanded.shortfall == 472
    assert "no change worth relaying" in str(demanded)


def test_a_withheld_output_that_is_dust_is_counted_as_dust_not_as_waiting():
    wallet = [utxo(50_000, tag="a"), utxo(900, tag="b", confirmations=0)]

    result = select_largest_first(
        wallet,
        request(fee_rate=DEAR_FEE_RATE),
        policy=SelectionPolicy(min_confirmations=1),
    )

    assert isinstance(result, SelectionFailure)
    assert result.reason is FailureReason.INSUFFICIENT_FUNDS
    assert result.candidate_count == 2
    assert result.eligible_count == 1
    assert result.withheld_count == 1
    assert result.withheld_value == 0
    assert result.dust_count == 1
    assert result.available == 50_000


def test_a_remainder_under_the_dust_threshold_is_given_to_the_fee():
    result = select_largest_first([utxo(100_300)], request())

    assert isinstance(result, Selection)
    assert result.change == 0
    assert result.fee == 300
    assert result.vsize == CHANGELESS_VSIZE
    assert result.fee - estimate_fee(result.vsize, FEE_RATE) == 108


def test_a_remainder_of_one_is_returned_when_nothing_counts_as_dust():
    result = select_largest_first(
        [utxo(100_227)], request(dust_threshold=0)
    )

    assert isinstance(result, Selection)
    assert result.change == 1
    assert result.fee == CHANGE_VSIZE
    assert result.vsize == CHANGE_VSIZE


def test_a_remainder_of_zero_does_not_create_an_empty_change_output():
    result = select_largest_first(
        [utxo(100_226)], request(dust_threshold=0)
    )

    assert isinstance(result, Selection)
    assert not result.has_change
    assert result.vsize == CHANGELESS_VSIZE


def test_require_change_keeps_adding_inputs_until_the_change_clears_dust():
    wallet = [utxo(100_300, tag="a"), utxo(50_000, tag="b")]

    result = select_largest_first(
        wallet, request(change_policy=ChangePolicy.REQUIRE_CHANGE)
    )

    assert isinstance(result, Selection)
    assert len(result.inputs) == 2
    assert result.change == 49_926
    assert result.fee == 374
    assert result.vsize == 374


def test_forbid_change_gives_the_whole_remainder_to_the_fee():
    result = select_largest_first(
        [utxo(200_000)], request(change_policy=ChangePolicy.FORBID_CHANGE)
    )

    assert isinstance(result, Selection)
    assert not result.has_change
    assert result.fee == 100_000
    assert result.vsize == CHANGELESS_VSIZE


def test_a_zero_fee_rate_pays_the_targets_exactly():
    exact = select_largest_first([utxo(100_000)], request(fee_rate=0))
    with_change = select_largest_first([utxo(200_000)], request(fee_rate=0))

    assert isinstance(exact, Selection)
    assert exact.fee == 0
    assert exact.change == 0

    assert isinstance(with_change, Selection)
    assert with_change.fee == 0
    assert with_change.change == 100_000


@pytest.mark.parametrize("overshoot", [0, COST_OF_CHANGE])
def test_branch_and_bound_accepts_an_overshoot_up_to_the_cost_of_change(
    overshoot,
):
    value = 100_000 + CHANGELESS_VSIZE + overshoot

    result = select_branch_and_bound(
        [utxo(value)], request(dust_threshold=0)
    )

    assert isinstance(result, Selection)
    assert not result.has_change
    assert result.fee == CHANGELESS_VSIZE + overshoot
    assert result.vsize == CHANGELESS_VSIZE


def test_one_base_unit_past_the_tolerance_returns_change_instead():
    value = 100_000 + CHANGELESS_VSIZE + COST_OF_CHANGE + 1

    result = select_branch_and_bound(
        [utxo(value)], request(dust_threshold=0)
    )

    assert isinstance(result, Selection)
    assert result.has_change
    assert result.change == 149
    assert result.fee == CHANGE_VSIZE


def test_a_spent_search_budget_falls_back_to_the_baseline():
    wallet = [utxo(100_374)]
    asked = request(dust_threshold=0)

    searched = select_branch_and_bound(wallet, asked)
    budgetless = select_branch_and_bound(wallet, asked, max_tries=0)

    assert isinstance(searched, Selection)
    assert not searched.has_change
    assert budgetless == select_largest_first(wallet, asked)
    assert budgetless.change == 148


def test_require_change_is_delegated_to_the_baseline_unchanged():
    wallet = [utxo(200_000, tag="a"), utxo(100_192, tag="b")]

    searched = select_branch_and_bound(wallet, request())
    demanded = select_branch_and_bound(
        wallet, request(change_policy=ChangePolicy.REQUIRE_CHANGE)
    )

    assert isinstance(searched, Selection)
    assert not searched.has_change
    assert searched.inputs == (wallet[1],)

    assert demanded == select_largest_first(
        wallet, request(change_policy=ChangePolicy.REQUIRE_CHANGE)
    )
    assert demanded.inputs == (wallet[0],)
    assert demanded.change == 99_774


@pytest.mark.parametrize(
    "kwargs", [{"max_tries": -1}, {"change_spend_script_size": 0}]
)
def test_branch_and_bound_refuses_a_budget_or_a_size_out_of_range(kwargs):
    with pytest.raises(ValueError):
        select_branch_and_bound([utxo(200_000)], request(), **kwargs)


@pytest.mark.parametrize("budget", [True, 1.0, None])
def test_branch_and_bound_refuses_a_budget_that_is_not_a_plain_int(budget):
    with pytest.raises(TypeError):
        select_branch_and_bound([utxo(200_000)], request(), max_tries=budget)


@strategies
def test_the_same_output_offered_twice_is_a_caller_bug(strategy):
    spent = utxo(200_000)

    with pytest.raises(ValueError):
        strategy([spent, spent], request())


@strategies
def test_something_that_is_not_an_output_is_a_caller_bug(strategy):
    with pytest.raises(TypeError):
        strategy([utxo(200_000), 200_000], request())


@strategies
def test_the_candidates_may_arrive_as_an_iterator(strategy):
    result = strategy(iter([utxo(200_000)]), request())

    assert isinstance(result, Selection)
