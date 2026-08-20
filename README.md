# utxo-select

Coin selection for UTXO chains: pick inputs, compute change, estimate fees, and
explain why a selection failed.

## Status

Pre-alpha. The models and size estimation are in place; the selection
algorithms are not implemented yet.

## Installation

```bash
pip install utxo-select
```

From a checkout:

```bash
pip install -e .
```

## Usage

Amounts, sizes and fee rates are integers of base units — satoshi arithmetic
never touches float.

```python
from utxo_select import ChangePolicy, SelectionRequest, Target, Utxo

utxos = [
    Utxo(txid="a" * 64, vout=0, value=120_000, confirmations=6),
    Utxo(txid="b" * 64, vout=1, value=45_000, confirmations=1),
]

request = SelectionRequest(
    targets=(Target(value=100_000),),
    fee_rate=12_000,  # per 1000 virtual bytes
    dust_threshold=546,
    change_policy=ChangePolicy.ALLOW_CHANGE,
)

print(request.total_target_value)
```

### Size and fee estimation

Virtual size follows from how many inputs and outputs a transaction has and
what script type each one is. Estimates are upper bounds, and both the virtual
size and the fee are rounded up: underpaying is what leaves a transaction stuck
in the mempool.

```python
from utxo_select import ScriptType, estimate_fee, estimate_vsize

vsize = estimate_vsize(
    inputs=[ScriptType.P2WPKH, ScriptType.P2WPKH],
    outputs=[ScriptType.P2TR, ScriptType.P2WPKH],
)

print(vsize, estimate_fee(vsize, fee_rate=12_000))
print(ScriptType.P2PKH.input_vsize)  # marginal cost of one more legacy input
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## License

MIT — see [LICENSE](LICENSE).

Maintained by [polycratia](https://polycratia.com).
