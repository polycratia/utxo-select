"""How a selection is priced: by script type through weight, or by a hint.

The numbers here are written out rather than derived, because the point of the
weight path is what a per-input size cannot see: the witness discount, the
marker and flag billed once for the transaction, and the fraction of a byte
that rounding every input on its own would add.
"""

from utxo_select import (
    ChangePolicy,
    ScriptType,
    Selection,
    SelectionRequest,
    Target,
    Utxo,
    estimate_fee,
    select_branch_and_bound,
    select_largest_first,
)

FEE_RATE = 12_000

# version 4 + input count 1 + output count 1 + locktime 4, no witness.
TX_OVERHEAD_VSIZE = 10


def hinted_wallet(value=200_000):
    return [Utxo(txid="a" * 64, vout=0, value=value, confirmations=6)]


def typed_wallet(script_type, value=200_000):
    return [
        Utxo(
            txid="a" * 64,
            vout=0,
            value=value,
            confirmations=6,
            script_type=script_type,
        )
    ]


def request(
    *, target_type=None, change_type=None, fee_rate=FEE_RATE, value=100_000
):
    return SelectionRequest(
        targets=(Target(value=value, script_type=target_type),),
        fee_rate=fee_rate,
        dust_threshold=546,
        change_policy=ChangePolicy.ALLOW_CHANGE,
        change_script_type=change_type,
    )


def test_a_size_hint_and_an_explicit_legacy_type_are_billed_alike():
    hinted = select_largest_first(hinted_wallet(), request())
    typed = select_largest_first(
        typed_wallet(ScriptType.P2PKH),
        request(target_type=ScriptType.P2PKH, change_type=ScriptType.P2PKH),
    )

    assert isinstance(hinted, Selection)
    assert isinstance(typed, Selection)
    assert hinted.vsize == 226
    assert hinted.fee == 2_712
    assert typed.vsize == hinted.vsize
    assert typed.fee == hinted.fee
    assert typed.change == hinted.change


def test_the_witness_discount_reaches_the_fee():
    result = select_largest_first(
        typed_wallet(ScriptType.P2WPKH),
        request(target_type=ScriptType.P2WPKH, change_type=ScriptType.P2WPKH),
    )

    assert isinstance(result, Selection)
    assert result.vsize == 141
    assert result.fee == 1_692
    assert result.change == 200_000 - 100_000 - 1_692


def test_the_marker_and_flag_are_billed_once_for_the_transaction():
    wallet = [
        Utxo(
            txid="a" * 64,
            vout=0,
            value=70_000,
            confirmations=6,
            script_type=ScriptType.P2WPKH,
        ),
        Utxo(
            txid="b" * 64,
            vout=0,
            value=60_000,
            confirmations=6,
            script_type=ScriptType.P2PKH,
        ),
    ]

    result = select_largest_first(
        wallet,
        request(
            target_type=ScriptType.P2PKH,
            change_type=ScriptType.P2PKH,
            fee_rate=1_000,
        ),
    )

    priced_apart = (
        TX_OVERHEAD_VSIZE
        + ScriptType.P2WPKH.input_vsize
        + ScriptType.P2PKH.input_vsize
        + 2 * ScriptType.P2PKH.output_vsize
    )

    assert isinstance(result, Selection)
    assert len(result.inputs) == 2
    assert priced_apart == 294
    assert result.vsize == 295
    assert result.fee == 295


def test_rounding_once_does_not_charge_for_a_fraction_of_a_byte():
    wallet = [
        Utxo(
            txid=tag * 64,
            vout=0,
            value=40_000,
            confirmations=6,
            script_type=ScriptType.P2TR,
        )
        for tag in "abc"
    ]

    result = select_largest_first(
        wallet,
        request(
            target_type=ScriptType.P2TR,
            change_type=ScriptType.P2TR,
            fee_rate=1_000,
        ),
    )

    priced_apart = (
        TX_OVERHEAD_VSIZE
        + 3 * ScriptType.P2TR.input_vsize
        + 2 * ScriptType.P2TR.output_vsize
    )

    assert isinstance(result, Selection)
    assert len(result.inputs) == 3
    assert priced_apart == 270
    assert result.vsize == 269


def test_a_hinted_input_and_a_typed_target_share_one_transaction():
    result = select_largest_first(
        hinted_wallet(), request(target_type=ScriptType.P2WPKH, fee_rate=1_000)
    )

    assert isinstance(result, Selection)
    assert result.vsize == 223
    assert result.fee == 223


def test_branch_and_bound_prices_a_changeless_match_by_weight():
    result = select_branch_and_bound(
        typed_wallet(ScriptType.P2WPKH, value=101_500),
        request(target_type=ScriptType.P2WPKH, change_type=ScriptType.P2WPKH),
    )

    assert isinstance(result, Selection)
    assert not result.has_change
    assert result.vsize == 110
    assert result.fee == 1_500
    assert result.fee >= estimate_fee(result.vsize, FEE_RATE)
