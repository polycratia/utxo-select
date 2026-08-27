# utxo-select

Coin selection for UTXO chains: pick inputs, compute change, estimate fees, and
explain why a selection failed.

## Status

Pre-alpha. The models, size estimation, largest-first and branch-and-bound are
in place; the remaining strategies are not implemented yet.

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

### Selecting inputs

Largest-first is the baseline strategy: candidates are taken by descending
value until the targets and the fee they imply are covered. A selection either
comes back balanced — inputs equal targets plus change plus fee — or it comes
back as a failure that says what was missing. It never underpays quietly.

```python
from utxo_select import Selection, select_largest_first

result = select_largest_first(utxos, request)

if isinstance(result, Selection):
    print([utxo.outpoint for utxo in result.inputs])
    print(result.fee, result.change, result.vsize)
else:
    print(result)  # e.g. insufficient_funds: ... short by ...
    print(result.reason, result.shortfall)
```

A remainder too small to be worth an output is given to the fee instead, unless
the change policy forbids that: `REQUIRE_CHANGE` keeps adding inputs until the
change clears the dust threshold, and `FORBID_CHANGE` never creates a change
output at all.

### Spending without change

Branch-and-bound searches for a subset of the candidates that pays the targets
and the fee exactly, leaving nothing to return. Dropping the change output
saves its fee now and the fee of spending it later, so a solution is accepted
while it overshoots by less than those two together. Candidates are weighed by
effective value, which is what an output is worth after the fee for spending
it, and an output that costs more to spend than it holds is left alone.

```python
from utxo_select import select_branch_and_bound

result = select_branch_and_bound(utxos, request)

if isinstance(result, Selection) and not result.has_change:
    print("changeless", result.fee, result.vsize)
```

Exact matches are the exception, not the rule. When the search budget runs out
without one — it defaults to 100000 nodes and is tunable with `max_tries` —
the largest-first result is returned instead, so the caller always gets the
best available answer rather than a failure.

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
