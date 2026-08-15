# utxo-select

Coin selection for UTXO chains: pick inputs, compute change, estimate fees, and
explain why a selection failed.

## Status

Pre-alpha. The package is installable but the selection algorithms are not
implemented yet.

## Installation

```bash
pip install utxo-select
```

From a checkout:

```bash
pip install -e .
```

## Usage

```python
import utxo_select

print(utxo_select.__version__)
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## License

MIT — see [LICENSE](LICENSE).
