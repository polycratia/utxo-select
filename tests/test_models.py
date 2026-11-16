"""The models: what they accept, what they refuse, and what they derive.

Satoshi arithmetic never touches float, so construction is the place where a
non-integer amount has to be stopped: a bool would quietly stand in for one
base unit, and a float for an amount that cannot be paid. The cases below walk
the validated fields of every model, including the boundary each range check
sits on.
"""

import dataclasses

import pytest

from utxo_select import (
    DEFAULT_INPUT_SCRIPT_SIZE,
    DEFAULT_OUTPUT_SCRIPT_SIZE,
    ChangePolicy,
    ScriptType,
    SelectionRequest,
    Target,
    Utxo,
)

TXID = "a" * 64
BASE_UTXO = {"txid": TXID, "vout": 0, "value": 1_000}
BASE_REQUEST = {"targets": (Target(value=1_000),), "fee_rate": 1_000}
NOT_PLAIN_INTS = (True, 1.0, "1", None)


def test_an_output_derives_its_outpoint_and_its_depth():
    utxo = Utxo(txid=TXID, vout=3, value=1_000)

    assert utxo.outpoint == f"{TXID}:3"
    assert utxo.is_confirmed is False
    assert Utxo(txid=TXID, vout=0, value=1, confirmations=1).is_confirmed is True


def test_a_shape_is_a_size_hint_until_a_type_is_named():
    assert Utxo(**BASE_UTXO).script_size == DEFAULT_INPUT_SCRIPT_SIZE
    assert Utxo(**BASE_UTXO).script_type is None
    assert Target(value=1).script_size == DEFAULT_OUTPUT_SCRIPT_SIZE
    assert SelectionRequest(**BASE_REQUEST).change_script_size == (
        DEFAULT_OUTPUT_SCRIPT_SIZE
    )
    assert SelectionRequest(**BASE_REQUEST).change_script_type is None


def test_the_smallest_amounts_the_models_allow_are_accepted():
    assert Utxo(txid=TXID, vout=0, value=1, script_size=1).value == 1
    assert Target(value=1, script_size=1).value == 1

    request = SelectionRequest(
        targets=(Target(value=1),),
        fee_rate=0,
        dust_threshold=0,
        change_script_size=1,
    )
    assert request.fee_rate == 0
    assert request.dust_threshold == 0


@pytest.mark.parametrize(
    "field, amount",
    [
        ("vout", -1),
        ("value", 0),
        ("value", -1),
        ("confirmations", -1),
        ("script_size", 0),
    ],
)
def test_an_output_refuses_an_amount_out_of_range(field, amount):
    with pytest.raises(ValueError):
        Utxo(**{**BASE_UTXO, field: amount})


@pytest.mark.parametrize(
    "field", ["vout", "value", "confirmations", "script_size"]
)
@pytest.mark.parametrize("amount", NOT_PLAIN_INTS)
def test_an_output_refuses_an_amount_that_is_not_a_plain_int(field, amount):
    with pytest.raises(TypeError):
        Utxo(**{**BASE_UTXO, field: amount})


@pytest.mark.parametrize("txid", ["", 123, None, b"a" * 64])
def test_an_output_refuses_a_txid_that_is_not_a_non_empty_string(txid):
    with pytest.raises(ValueError):
        Utxo(**{**BASE_UTXO, "txid": txid})


@pytest.mark.parametrize("script_type", ["p2pkh", 0, Utxo])
def test_a_shape_refuses_a_type_that_is_not_a_script_type(script_type):
    with pytest.raises(TypeError):
        Utxo(**{**BASE_UTXO, "script_type": script_type})
    with pytest.raises(TypeError):
        Target(value=1_000, script_type=script_type)
    with pytest.raises(TypeError):
        SelectionRequest(**BASE_REQUEST, change_script_type=script_type)


@pytest.mark.parametrize("script_type", list(ScriptType))
def test_every_script_type_is_accepted_wherever_a_shape_is_described(
    script_type,
):
    assert Utxo(**BASE_UTXO, script_type=script_type).script_type is script_type
    assert Target(value=1, script_type=script_type).script_type is script_type
    request = SelectionRequest(**BASE_REQUEST, change_script_type=script_type)
    assert request.change_script_type is script_type


@pytest.mark.parametrize("amount", [0, -1])
def test_a_target_refuses_a_value_or_a_hint_out_of_range(amount):
    with pytest.raises(ValueError):
        Target(value=amount)
    with pytest.raises(ValueError):
        Target(value=1_000, script_size=amount)


@pytest.mark.parametrize("amount", NOT_PLAIN_INTS)
def test_a_target_refuses_an_amount_that_is_not_a_plain_int(amount):
    with pytest.raises(TypeError):
        Target(value=amount)
    with pytest.raises(TypeError):
        Target(value=1_000, script_size=amount)


def test_a_request_without_a_target_pays_nothing_and_is_refused():
    with pytest.raises(ValueError):
        SelectionRequest(targets=(), fee_rate=1_000)


@pytest.mark.parametrize("target", [1_000, "1000", None, (1_000,)])
def test_a_request_refuses_a_target_that_is_not_a_target(target):
    with pytest.raises(TypeError):
        SelectionRequest(targets=(Target(value=1), target), fee_rate=1_000)


def test_a_request_keeps_its_targets_as_a_tuple_however_they_arrive():
    from_list = SelectionRequest(targets=[Target(value=7)], fee_rate=1_000)
    from_iterator = SelectionRequest(
        targets=iter([Target(value=7)]), fee_rate=1_000
    )

    assert from_list.targets == (Target(value=7),)
    assert from_iterator.targets == from_list.targets


def test_a_request_adds_up_what_it_is_paying():
    request = SelectionRequest(
        targets=(Target(value=100_000), Target(value=2_500), Target(value=1)),
        fee_rate=1_000,
    )

    assert request.total_target_value == 102_501


@pytest.mark.parametrize(
    "field, amount",
    [("fee_rate", -1), ("dust_threshold", -1), ("change_script_size", 0)],
)
def test_a_request_refuses_an_amount_out_of_range(field, amount):
    with pytest.raises(ValueError):
        SelectionRequest(**{**BASE_REQUEST, field: amount})


@pytest.mark.parametrize(
    "field", ["fee_rate", "dust_threshold", "change_script_size"]
)
@pytest.mark.parametrize("amount", NOT_PLAIN_INTS)
def test_a_request_refuses_an_amount_that_is_not_a_plain_int(field, amount):
    with pytest.raises(TypeError):
        SelectionRequest(**{**BASE_REQUEST, field: amount})


@pytest.mark.parametrize("policy", ["allow_change", None, 0])
def test_a_request_refuses_a_change_policy_that_is_not_one(policy):
    with pytest.raises(TypeError):
        SelectionRequest(**BASE_REQUEST, change_policy=policy)


@pytest.mark.parametrize("policy", list(ChangePolicy))
def test_every_change_policy_is_accepted(policy):
    assert (
        SelectionRequest(**BASE_REQUEST, change_policy=policy).change_policy
        is policy
    )


def test_a_model_cannot_be_mutated_after_construction():
    utxo = Utxo(**BASE_UTXO)
    target = Target(value=1_000)
    request = SelectionRequest(**BASE_REQUEST)

    with pytest.raises(dataclasses.FrozenInstanceError):
        utxo.value = 2_000
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.value = 2_000
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.fee_rate = 2_000
