# utxo-select

Coin selection for UTXO chains: pick inputs, compute change, estimate fees, and
explain why a selection failed.

## Status

Pre-alpha. The models are in place; the selection algorithms are not implemented
yet.

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

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## License

MIT — see [LICENSE](LICENSE).

Maintained by [polycratia](https://polycratia.com).
