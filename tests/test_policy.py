"""The policy value object: what it accepts, and when it consolidates."""

import dataclasses

import pytest

from utxo_select import (
    DEFAULT_CONSOLIDATION_FEE_RATE,
    DEFAULT_POLICY,
    InputPreference,
    SelectionPolicy,
    Utxo,
)


def utxo(value=100_000, *, confirmations=0):
    return Utxo(txid="a" * 64, vout=0, value=value, confirmations=confirmations)


def test_the_default_policy_spends_anything_largest_first():
    assert DEFAULT_POLICY.min_confirmations == 0
    assert DEFAULT_POLICY.input_preference is InputPreference.FEWER_INPUTS
    assert DEFAULT_POLICY.consolidation_fee_rate == DEFAULT_CONSOLIDATION_FEE_RATE
    assert DEFAULT_POLICY.accepts(utxo(confirmations=0))


@pytest.mark.parametrize(
    "required, confirmations, accepted",
    [(0, 0, True), (1, 0, False), (1, 1, True), (6, 5, False), (6, 6, True)],
)
def test_accepts_measures_depth_against_min_confirmations(
    required, confirmations, accepted
):
    policy = SelectionPolicy(min_confirmations=required)
    assert policy.accepts(utxo(confirmations=confirmations)) is accepted


@pytest.mark.parametrize(
    "preference, fee_rate, consolidates",
    [
        (InputPreference.FEWER_INPUTS, 1, False),
        (InputPreference.FEWER_INPUTS, 10_000, False),
        (InputPreference.MORE_INPUTS, 1, True),
        (InputPreference.MORE_INPUTS, 10_000, True),
        (InputPreference.CONSOLIDATE_WHEN_CHEAP, 1_999, True),
        (InputPreference.CONSOLIDATE_WHEN_CHEAP, 2_000, True),
        (InputPreference.CONSOLIDATE_WHEN_CHEAP, 2_001, False),
    ],
)
def test_consolidates_at_reads_the_fee_rate(preference, fee_rate, consolidates):
    policy = SelectionPolicy(
        input_preference=preference, consolidation_fee_rate=2_000
    )
    assert policy.consolidates_at(fee_rate) is consolidates


def test_a_policy_cannot_be_mutated_after_construction():
    policy = SelectionPolicy(min_confirmations=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.min_confirmations = 0


@pytest.mark.parametrize("depth", [-1, -100])
def test_negative_depth_is_rejected(depth):
    with pytest.raises(ValueError):
        SelectionPolicy(min_confirmations=depth)


@pytest.mark.parametrize("depth", [True, 1.0, "6", None])
def test_a_depth_that_is_not_a_plain_int_is_rejected(depth):
    with pytest.raises(TypeError):
        SelectionPolicy(min_confirmations=depth)


def test_a_negative_consolidation_fee_rate_is_rejected():
    with pytest.raises(ValueError):
        SelectionPolicy(consolidation_fee_rate=-1)


def test_a_preference_that_is_not_an_input_preference_is_rejected():
    with pytest.raises(TypeError):
        SelectionPolicy(input_preference="fewer_inputs")
