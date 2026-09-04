"""Policies applied through the strategies that take them as a parameter."""

import pytest

from utxo_select import (
    ChangePolicy,
    FailureReason,
    InputPreference,
    Selection,
    SelectionFailure,
    SelectionPolicy,
    SelectionRequest,
    Target,
    Utxo,
    select_branch_and_bound,
    select_largest_first,
)

FEE_RATE = 1_000


def utxo(value, *, confirmations=6, tag="a"):
    return Utxo(txid=tag * 64, vout=0, value=value, confirmations=confirmations)


def request(value=100_000, *, fee_rate=FEE_RATE):
    return SelectionRequest(
        targets=(Target(value=value),),
        fee_rate=fee_rate,
        change_policy=ChangePolicy.ALLOW_CHANGE,
    )


def small_pool():
    return [
        utxo(30_000, tag="a"),
        utxo(40_000, tag="b"),
        utxo(50_000, tag="c"),
        utxo(90_000, tag="d"),
    ]


def values(result):
    return sorted(chosen.value for chosen in result.inputs)


def test_the_default_policy_spends_an_unconfirmed_output():
    deep = utxo(200_000, tag="a")
    shallow = utxo(300_000, confirmations=0, tag="b")

    result = select_largest_first([deep, shallow], request())

    assert isinstance(result, Selection)
    assert result.inputs == (shallow,)


def test_min_confirmations_holds_a_shallow_output_back():
    deep = utxo(200_000, tag="a")
    shallow = utxo(300_000, confirmations=0, tag="b")

    result = select_largest_first(
        [deep, shallow], request(), policy=SelectionPolicy(min_confirmations=1)
    )

    assert isinstance(result, Selection)
    assert result.inputs == (deep,)


def test_fewer_inputs_covers_the_targets_with_the_largest_outputs():
    result = select_largest_first(small_pool(), request())

    assert isinstance(result, Selection)
    assert values(result) == [50_000, 90_000]


def test_more_inputs_sweeps_the_smallest_outputs_instead():
    policy = SelectionPolicy(input_preference=InputPreference.MORE_INPUTS)

    result = select_largest_first(small_pool(), request(), policy=policy)

    assert isinstance(result, Selection)
    assert values(result) == [30_000, 40_000, 50_000]


@pytest.mark.parametrize(
    "fee_rate, expected",
    [
        (1_000, [30_000, 40_000, 50_000]),
        (2_000, [30_000, 40_000, 50_000]),
        (5_000, [50_000, 90_000]),
    ],
)
def test_consolidate_when_cheap_switches_at_its_fee_rate(fee_rate, expected):
    policy = SelectionPolicy(
        input_preference=InputPreference.CONSOLIDATE_WHEN_CHEAP,
        consolidation_fee_rate=2_000,
    )

    result = select_largest_first(
        small_pool(), request(fee_rate=fee_rate), policy=policy
    )

    assert isinstance(result, Selection)
    assert values(result) == expected


def test_dust_is_dropped_whichever_way_the_policy_leans():
    dust = utxo(100, tag="e")
    policy = SelectionPolicy(input_preference=InputPreference.MORE_INPUTS)

    result = select_largest_first([dust, *small_pool()], request(), policy=policy)

    assert isinstance(result, Selection)
    assert dust not in result.inputs
    assert values(result) == [30_000, 40_000, 50_000]


def test_a_wallet_that_only_needs_to_wait_is_told_so():
    deep = utxo(50_000, tag="a")
    shallow = utxo(100_000, confirmations=0, tag="b")

    result = select_largest_first(
        [deep, shallow],
        request(120_000),
        policy=SelectionPolicy(min_confirmations=1),
    )

    assert isinstance(result, SelectionFailure)
    assert result.reason is FailureReason.INSUFFICIENT_CONFIRMATIONS
    assert result.candidate_count == 2
    assert result.eligible_count == 1
    assert result.withheld_count == 1
    assert result.withheld_value == 100_000
    assert result.available == 50_000
    assert result.fee == 192
    assert result.shortfall == 70_192
    assert "confirmations" in str(result)


def test_waiting_that_would_not_have_paid_is_still_a_funding_failure():
    deep = utxo(50_000, tag="a")
    shallow = utxo(10_000, confirmations=0, tag="b")

    result = select_largest_first(
        [deep, shallow],
        request(120_000),
        policy=SelectionPolicy(min_confirmations=1),
    )

    assert isinstance(result, SelectionFailure)
    assert result.reason is FailureReason.INSUFFICIENT_FUNDS
    assert result.withheld_count == 1
    assert result.withheld_value == 10_000
    assert result.shortfall == 70_000


def test_branch_and_bound_leaves_shallow_outputs_alone():
    shallow = utxo(100_200, confirmations=0, tag="a")
    deep = utxo(100_200, tag="b")

    unguarded = select_branch_and_bound([deep, shallow], request())
    guarded = select_branch_and_bound(
        [deep, shallow], request(), policy=SelectionPolicy(min_confirmations=1)
    )

    assert isinstance(unguarded, Selection)
    assert unguarded.inputs == (shallow,)
    assert unguarded.change == 0
    assert isinstance(guarded, Selection)
    assert guarded.inputs == (deep,)
    assert guarded.change == 0


def test_branch_and_bound_searches_in_the_order_the_policy_wants():
    large = utxo(100_252, tag="c")
    small_one = utxo(50_200, tag="a")
    small_two = utxo(50_200, tag="b")
    pool = [large, small_one, small_two]

    fewer = select_branch_and_bound(pool, request())
    more = select_branch_and_bound(
        pool,
        request(),
        policy=SelectionPolicy(input_preference=InputPreference.MORE_INPUTS),
    )

    assert isinstance(fewer, Selection)
    assert fewer.inputs == (large,)
    assert fewer.change == 0
    assert isinstance(more, Selection)
    assert values(more) == [50_200, 50_200]
    assert more.change == 0


def test_branch_and_bound_falls_back_under_the_same_policy():
    deep = utxo(200_000, tag="a")
    shallow = utxo(500_000, confirmations=0, tag="b")

    result = select_branch_and_bound(
        [deep, shallow], request(), policy=SelectionPolicy(min_confirmations=1)
    )

    assert isinstance(result, Selection)
    assert result.inputs == (deep,)
    assert result.has_change
