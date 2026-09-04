# utxo-select

Coin selection for UTXO chains: pick inputs, compute change, estimate fees, and
explain why a selection failed.

## Status

Pre-alpha. The models, size estimation, largest-first, branch-and-bound and the
selection policies are in place; the remaining strategies are not implemented
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
    print(result)  # e.g. insufficient_after_fees: ... short by ...
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

### Selection policies

Two selections can pay the same targets at the same fee rate and still differ
in what they spend: whether an output that is not yet buried deeply enough was
used, how many of the wallet's outputs were tied together in one transaction
for anyone reading the chain, and whether a cheap block was spent tidying up.
That is a policy, and it is a parameter of the strategies above rather than a
strategy of its own — it filters the candidates and fixes the order they are
tried in, and the algorithm on top is the same one either way.

```python
from utxo_select import InputPreference, SelectionPolicy

policy = SelectionPolicy(
    min_confirmations=6,
    input_preference=InputPreference.CONSOLIDATE_WHEN_CHEAP,
    consolidation_fee_rate=2_000,  # per 1000 virtual bytes
)

result = select_largest_first(utxos, request, policy=policy)
```

| `input_preference` | Candidates are taken |
| --- | --- |
| `FEWER_INPUTS` | largest first, so the fewest outputs are linked together |
| `MORE_INPUTS` | smallest first, sweeping small outputs into one spend |
| `CONSOLIDATE_WHEN_CHEAP` | smallest first at or below `consolidation_fee_rate`, largest first above it |

`min_confirmations` is the depth an output must have reached before it may be
spent; the default of zero accepts anything, mempool included. Outputs held
back are not forgotten — if the wallet would have paid with them, the failure
comes back as `insufficient_confirmations` carrying `withheld_value`, which is
answered by waiting rather than by funding.

Both strategies take `policy=`, and `DEFAULT_POLICY` — spend anything, largest
first — is what a caller gets without asking for one.

### When a selection fails

A failure is a returned value, not an exception, and it names which of five
things went wrong. They are worth telling apart: some are answered by funding
the wallet, one only by waiting, and the rest by changing the request.

| `reason` | What happened |
| --- | --- |
| `insufficient_funds` | the candidates do not hold the targets, fee aside |
| `insufficient_after_fees` | they hold the targets but not the fee on top |
| `dust_only` | every candidate costs more to spend than it holds |
| `change_below_dust` | they can pay, but leave no change worth relaying |
| `insufficient_confirmations` | they hold enough, but not deeply enough confirmed |

The numbers behind the verdict come with it: `available` against `required`
and the `shortfall` between them, the `fee` a transaction spending every
candidate would owe, the `target_value` asked for, how many candidates were
worth spending at all, and how many the policy held back.

```python
from utxo_select import FailureReason, SelectionFailure

result = select_largest_first(utxos, request)

if isinstance(result, SelectionFailure):
    print(result.available, result.required, result.shortfall, result.fee)
    print(result.spendable_count, "of", result.candidate_count, "spendable")
    if result.reason is FailureReason.INSUFFICIENT_AFTER_FEES:
        print("a lower fee rate closes a gap of", result.shortfall)
    if result.reason is FailureReason.INSUFFICIENT_CONFIRMATIONS:
        print(result.withheld_value, "waits on", result.withheld_count, "outputs")
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
pip install -e ".[dev]"
python -m pytest
```

## License

MIT — see [LICENSE](LICENSE).

Maintained by [polycratia](https://polycratia.com).
