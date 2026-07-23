# fincosys_sync

Sync fincosys financial-forensics data (bank transactions across the 22+
RegimA/Faucitt-matter group entities, case 2025-137857) into a GnuCash book,
via the SWIG Python bindings shipped in this repo (`bindings/python/`).

## Purpose

`fincosys` is the financial ground-truth repository for the case: ~22,055
reconciled bank-statement transactions across 24+ corporate entities (RST,
RWD, RSA, SLG, VVA, FFT, etc.), stored as flat JSON, not as a double-entry
ledger. This tool turns that data into a real GnuCash book so it can be
browsed, reported on, and cross-checked using GnuCash's own tooling
(reports, reconciliation, multi-currency handling) instead of ad hoc
scripts.

It is deliberately split into a **plan** step (pure Python, safe, produces
a reviewable JSON artifact) and an **apply** step (writes to a real GnuCash
book), so a human can review exactly what will be created/imported before
anything touches a book file or database.

## Data flow

```
fincosys/data/*.json                      (MASTER_ENTITIES.json,
  (22 entities, 22k+ txns,                 MASTER_ACCOUNTS.json,
   raw bank-statement lines)               transaction_index.json)
        |
        |  (preferred path, once it exists)
        v
fincosys-atomspace-builder                 GncSyncExporter builds a
  GnuCashSyncExporter                      hypergraph from fincosys data
        |                                  and exports a normalized,
        v                                  already-double-entry-balanced
gnucash_sync_feed.json                     "sync feed" (accounts +
  (schema_version 1.0)                     transactions, schema below)
        |
        |  --feed gnucash_sync_feed.json
        |  (OR, if the feed doesn't exist yet: --data-dir fincosys/data,
        |   using the fallback loader in this script, which builds an
        |   equivalent structure directly from the raw fincosys JSON)
        v
sync_fincosys.py --plan-only               Validates: balanced splits,
        |                                  duplicate txids, unknown
        v                                  account refs. Writes plan +
   sync_plan.json                          per-entity summary.
        |
        |  --apply --book <path>
        v
GnuCash book                               Account (Fincosys Import >
  (file / sqlite3 / postgres / mysql)      <entity> > <account>) tree +
                                            Transaction/Split objects,
                                            idempotent re-sync via txid.
```

`fincosys-atomspace-builder`'s `GnuCashSyncExporter`
(`atomspace_builder/exporters/gnucash_exporter.py`) now exists and emits
`gnucash_sync_feed.json` in exactly this shape -- prefer `--feed <path>`
pointed at its output over the `--data-dir` fallback loader below when a
sibling `fincosys-atomspace-builder` checkout is available. The two are
cross-verified: `tests/fixtures/atomspace_builder_sync_feed.json` is a real
document captured from that exporter's own test fixture (see
`tests/fixtures/README.md` for provenance/regeneration), and
`tests/test_atomspace_builder_feed.py` feeds it through `load_feed()` +
`build_plan()` and asserts the plan comes out clean -- so schema drift on
either side of the repo boundary fails this repo's test suite instead of
silently breaking at apply time. The `--data-dir` fallback loader remains
available for when no exporter output is at hand; its output is shaped
identically to the feed schema, so both paths are interchangeable
downstream of `load_plan_inputs()`.

### Sync feed schema (schema_version "1.0")

```json
{
  "schema_version": "1.0",
  "accounts": [
    {"code": "...", "name": "...", "parent_code": "... or null",
     "account_type": "ASSET|LIABILITY|BANK|EXPENSE|INCOME|EQUITY",
     "entity_code": "...", "currency": "ZAR", "description": "..."}
  ],
  "transactions": [
    {"txid": "...", "date": "YYYY-MM-DD", "description": "...",
     "currency": "ZAR", "entity_code": "...",
     "splits": [{"account_code": "...", "amount": 0.0, "memo": "..."},
                {"account_code": "...", "amount": 0.0, "memo": "..."}],
     "metadata": {"is_intercompany": true, "category": "...",
                  "xero_account_code": "..."}}
  ]
}
```

## Prerequisites

- **`--plan-only` (default mode)**: Python 3 standard library only. No
  compiled GnuCash required. This is the mode exercised by `tests/`.
- **`--apply`**: a GnuCash build with the SWIG Python bindings compiled
  and importable (`import gnucash` must work) -- see this repo's top-level
  build instructions and `bindings/python/README`. `sync_fincosys.py`
  detects a missing/broken `gnucash` module and raises a clear
  `ImportError` telling you what to do, instead of a confusing traceback.

## Usage

### Plan (dry run, review before touching a book)

```bash
cd bindings/python/example_scripts/fincosys_sync

# from the real fincosys data checkout, no exporter feed needed yet:
python3 sync_fincosys.py --data-dir /path/to/fincosys/data \
    --plan-only --out sync_plan.json

# once fincosys-atomspace-builder's GnuCashSyncExporter exists:
python3 sync_fincosys.py --feed /path/to/gnucash_sync_feed.json \
    --plan-only --out sync_plan.json
```

This prints a per-entity account/transaction count summary and a
validation report (duplicate txids, unknown account references, splits
that don't sum to ~0), and writes the full plan to `--out`. Nothing is
written to a GnuCash book in this mode, and the `gnucash` module is never
imported.

### Apply (write into a real GnuCash book)

```bash
# build the plan on the fly and apply it in one step:
python3 sync_fincosys.py --data-dir /path/to/fincosys/data \
    --apply --book /path/to/fincosys.gnucash

# or replay a previously-reviewed plan file:
python3 sync_fincosys.py --apply --book /path/to/fincosys.gnucash \
    --out sync_plan.json   # reads this plan instead of rebuilding it

# database book URLs work too, same as any other gnucash Python script:
python3 sync_fincosys.py --data-dir /path/to/fincosys/data --apply \
    --book 'postgres://user:pass@host/fincosys_gnc'
```

`--apply` opens or creates the book, builds the account tree (`Fincosys
Import` -> `<entity_code>` -> leaf accounts), and creates a
`Transaction`/`Split` pair for each planned transaction whose txid isn't
already present. By default it refuses to apply a plan that failed
validation; pass `--force` to apply anyway (invalid transactions are
individually skipped, not partially applied).

## The `Imbalance-<category>` placeholder account design

fincosys's `transaction_index.json` rows are **single-sided bank-statement
lines** -- each row is one side of a real-world transaction (a debit or a
credit on one entity's bank account), extracted from a bank statement PDF.
There is no natural double-entry counterpart recorded anywhere in the
source data (the counterparty's own books, if they exist at all, are a
different entity's separate statement corpus, and matching them up is a
much harder reconciliation problem that fincosys's own
`data/acct_recons/` work is still tackling).

GnuCash requires every `Transaction` to balance (all `Split` amounts sum
to zero). GnuCash's own OFX and QIF importers solve exactly this problem
for imported single-sided data by creating one `Imbalance-<currency>`
placeholder account per currency and posting the offsetting split there
(see `libgnucash/engine/Scrub.cpp`,
`xaccScrubUtilityGetOrMakeAccount(..., ACCT_TYPE_BANK, ...)`) -- so users
can immediately see, per account, how much of the imported data is
"unreconciled" and needs manual matching, without blocking the import.

This tool follows the same pattern, but scopes the placeholder further to
**one `Imbalance-<category>` account per entity per fincosys transaction
`category`** (`PAYMENT`, `INCOME`, `FEE`, `TRANSFER`, `CARD_PURCHASE`,
etc.) instead of one per currency. That gives a much more useful starting
point for reconciliation: "how much of RST's unreconciled `PAYMENT`
volume is there" is immediately answerable from the account tree, rather
than everything landing in one large bucket. Like GnuCash's own Imbalance
accounts, these placeholders use `ACCT_TYPE_BANK` (`account_type: "BANK"`
in the plan/feed schema) -- matching the real behavior of the accounts
being modeled (bank in, bank out) and keeping them out of the Income
Statement/Balance Sheet's Expense and Income roll-ups until someone
manually re-categorizes/re-splits an entry.

## `helix`: no sync source available, generic tool, out of scope

The wider ecosystem CLAUDE.md context for this case mentions
`E:/Appz/influernto/influernto` (uncharted/influent) as a visual
link-analysis frontend that fincosys exports to, and separately there is
a repository named `fincosys/helix`. **`fincosys/helix` was investigated
and found to be the generic, open-source VectorInstitute/helix
research-loop tool** -- it contains no financial data, no fincosys-specific
code, and no schema or API that this sync tool could plausibly target.
There is no integration between `fincosys_sync` and `helix`, and none is
planned. This is noted explicitly here, rather than fabricating a data
path through it, because it would be easy to assume from the name alone
that it's part of the fincosys data pipeline -- it is not.

## Idempotency (safe re-sync)

Every planned `Transaction` carries its fincosys `txid` (a stable,
content-derived identifier, e.g.
`txid-20200102-55270018789-227-0001-f1a0900b`). `--apply` writes that
txid into the `Transaction`'s `Num` field, prefixed with `fincosys:`
(`TXID_NUM_PREFIX` in `sync_fincosys.py`), e.g.
`fincosys:txid-20200102-...`.

Before creating any transaction, `--apply` scans the target leaf
account's existing splits, collects the `fincosys:`-prefixed `Num` values
already present, and skips any planned transaction whose txid is already
recorded. This makes re-running `--apply` against the same book (e.g.
after fincosys data is refreshed, or after retrying a partially-applied
sync) safe: previously-imported transactions are left untouched and only
new/changed txids are created. The account hierarchy is built the same
way -- `_find_or_make_child` looks up an existing child account by name
before creating a new one, so re-running `--apply` does not duplicate the
`Fincosys Import` / entity / leaf account tree either.

(The `Num` field was chosen over a GnuCash "slot"/KVP because it's
directly exposed by the stable SWIG API used throughout this repo's
`bindings/python/example_scripts/`; if slot access is available in your
build, `Transaction.SetSlot` under key e.g. `fincosys-txid` is an
equally valid, and more conventionally "metadata-shaped", place to store
this -- the two approaches are interchangeable for the skip-if-present
check this tool performs.)

## Tests

```bash
cd /path/to/gnucashm
python3 -m pytest bindings/python/example_scripts/fincosys_sync/tests/ -v
```

`tests/test_plan.py` is pure Python and never imports `gnucash` -- it
exercises `load_feed`/`build_plan`/`run_plan_only` against a small
synthetic fixture feed (4 accounts, 5 transaction entries under 4 distinct
txids: 2 balanced+valid, 1 duplicate txid, 1 referencing an unknown
account, 1 with unbalanced splits) and asserts the validator flags exactly
those issues.

A local `pytest.ini` in this directory pins `--import-mode=importlib` and
gives pytest a rootdir inside `fincosys_sync/` itself. This is necessary
because `bindings/python/__init__.py` unconditionally does
`from gnucash.gnucash_core import *`; without this, pytest's default
collection (rooted at the repo's `.git`) walks its collection tree
through that package on the way to `fincosys_sync/tests/`, importing it
as a side effect and failing wherever the compiled `gnucash` module isn't
available -- exactly the environment `--plan-only` and its tests are
supposed to work in.
