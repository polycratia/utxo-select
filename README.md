# utxo-select

Coin selection for UTXO chains: pick inputs, compute change, estimate fees, and
explain why a selection failed.

## Status

Pre-alpha. The models, size estimation, largest-first, branch-and-bound and the
selection policies are in place; the remaining strategies are not implemented
yet.

## What a selection decides

A wallet is a pile of outputs, and a payment has to be assembled out of some
subset of them. Which subset is taken is not an implementation detail: it fixes
what the transaction costs, what the wallet is left holding afterwards, and how
much of itself it shows to anyone reading the chain.

### The cost model in plain words

A transaction is billed for its size, not for its value. Paying a thousand
base units and paying a billion costs the same if the transaction is the same
shape.

Size comes in fixed pieces: about 10 virtual bytes of transaction overhead,
then 41 vbytes plus the unlocking script for every input, then 9 vbytes plus
the locking script for every output. With the legacy defaults these models
carry — 107 and 25 — an input is 148 vbytes and an output 34. At 12 base units
per vbyte, which is `fee_rate=12_000` per 1000 vbytes, that is 1_776 for each
input and 408 for each output, whatever they hold.

Two things follow, and most of this library is downstream of them.

**An output is worth less than it says.** Moving it costs 1_776, so an output
holding 45_000 brings 43_224 to the table. That is its effective value, and an
output holding less than the fee to spend it brings nothing at all at that rate.

**Change is paid for twice.** A change output costs 408 to create now and 1_776
to spend later, 2_184 together. A selection that overshoots the target by less
than that and gives the remainder to the fee has spent less than one that
returns it politely. That figure is the cost of change, and it is exactly the
tolerance branch-and-bound searches within.

### Three selections on the same wallet

```python
wallet = [
    Utxo(txid="a" * 64, vout=0, value=200_000, confirmations=12),
    Utxo(txid="b" * 64, vout=0, value=102_400, confirmations=12),
    Utxo(txid="c" * 64, vout=0, value=45_000, confirmations=12),
    Utxo(txid="d" * 64, vout=0, value=12_000, confirmations=12),
    Utxo(txid="e" * 64, vout=0, value=900, confirmations=12),
]

request = SelectionRequest(
    targets=(Target(value=100_000),),
    fee_rate=12_000,
    dust_threshold=546,
)
```

One wallet, one bill of 100_000, one fee rate — and three different answers:

| Selection | Inputs | `vsize` | `fee` | `change` |
| --- | --- | --- | --- | --- |
| `select_largest_first` | 200_000 | 226 | 2_712 | 97_288 |
| `select_branch_and_bound` | 102_400 | 192 | 2_400 | none |
| `select_largest_first`, `MORE_INPUTS` | 12_000 + 45_000 + 102_400 | 522 | 6_264 | 53_136 |

Largest-first spends the one big output and hands almost all of it back as
change. It is predictable and it is never the whole story: the cost is not the
2_712 on the receipt but 2_712 plus the 1_776 that the new change output will
cost to spend, 4_488 in all, and the wallet still holds every small output it
held before.

Branch-and-bound finds that one candidate covers the bill within the cost of
change on its own. The 96 base units it overshoots by go to the miner, no
change output is created, and nothing has to be spent again later: 2_400 total.
Matches like that are the exception rather than the rule, which is why the
search falls back to the largest-first answer instead of failing.

The consolidating policy pays 6_264 — more than twice the baseline — to sweep
three outputs into one spend. At this rate that is a poor trade. At 2 base
units per vbyte the same sweep costs 1_044 and buys a wallet that is cheap to
spend from when rates rise again, which is the bet `CONSOLIDATE_WHEN_CHEAP`
makes for a caller who does not want to time it by hand.

### Dust, and which threshold is meant

Two different limits share the word, and they fail in different directions.

Relay dust is `dust_threshold`, 546 by convention: the smallest output worth
creating at all, below which nodes will not pass the transaction on. A
remainder under it is given to the fee rather than returned, and under
`REQUIRE_CHANGE` inputs keep being added until the change clears it.

Economic dust is relative to the fee rate. The 900 output above holds more than
the relay threshold and is still unspendable at 12 per vbyte, because moving it
costs 1_776. Both strategies leave it alone, and a failure counts it under
`dust_count`. Nothing has been lost: at 2 per vbyte the same output costs 296 to
spend and is ordinary money again. Dust of this kind is waiting for a cheaper
block, not gone.

### Privacy is spent alongside the fee

Every input in a transaction is a public claim that one party could sign for
all of them. That is the most dependable inference chain analysis has, and it
is one-way: outputs joined in a transaction stay joined.

Fewer inputs link fewer of the wallet's outputs, which is what `FEWER_INPUTS`
buys and why it is the default. Consolidation is the opposite trade — a cheap
block in exchange for tying everything swept together, permanently. The advice
to consolidate when fees are low is sound and it is also the moment that
linkage is cheapest to give away.

A changeless spend removes a second inference rather than adding one: with no
change output there is nothing to guess about which output went back to the
sender. That is an argument for branch-and-bound past the fee it saves.

`min_confirmations` is a different kind of caution, about a spend built on an
output that could still be replaced rather than about what is observable.

None of this makes a wallet private on its own, and this library does not
pretend to decide the trade. It reports what a selection costs, what it links
and what it leaves behind, so that the decision is made with the numbers in
view.

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
