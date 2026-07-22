# Fincosys Ecosystem Sync — Status

This documents how gnucashm connects to the wider fincosys financial
ecosystem: `RegimA-Zone/fincosys-atomspace-builder`, `cogpy/fincosys`,
`fincosys/helix`, `cogpy/revstream1`, and `cogpy/ad-res-j7`.

## What already exists

`libgnucash/engine/gnc-fincosys-sync.h/.cpp` (see
[ORGANIZATION_ENHANCEMENTS.md](../ORGANIZATION_ENHANCEMENTS.md)) implements
the C++ side of the shared **Fincosys Ecosystem Sync Schema v1**
(`"schema": "fincosys-ecosystem-sync/v1"`):

- `gnc_organizations_to_fincosys_json()` — export `GncOrganization` +
  `Account` data.
- `gnc_organizations_from_fincosys_json()` — import organizations/accounts
  from a document in this schema.
- `gnc_transactions_from_syncfeed_json()` — import full double-entry
  accounts/transactions from fincosys-atomspace-builder's
  `GnuCashSyncExporter` feed.

These are exercised by
`libgnucash/engine/test/gtest-fincosys-sync.cpp`, but until now nothing
outside that test suite ever produced or consumed a real sync document.

## What this change adds

`scripts/sync_fincosys_ecosystem.py` drives
`fincosys-atomspace-builder`'s `gnucash_ecosystem` preset (fincosys master
data + gnucashm's own prior export, if any + gnucashcog-v3's cognitive
atoms + helix's manifest + revstream1's case-evidence records) and writes
the result to `data/fincosys_sync/gnucashm_ecosystem_sync.json` — a real,
schema-conformant document ready for `gnc_organizations_from_fincosys_json()`
to import into a `QofBook`.

`.github/workflows/sync-fincosys-ecosystem.yml` runs that script on manual
dispatch: dry-run by default (build + validate + print counts only), or
`write: true` to persist a snapshot and open a draft PR for review. It
never runs on a schedule and never commits without an explicit run.

```bash
# Local usage, with sibling checkouts of the repos above:
python scripts/sync_fincosys_ecosystem.py \
    --atomspace-builder-dir ../fincosys-atomspace-builder \
    --fincosys-data-dir ../fincosys/data \
    --helix-manifest ../helix/ecosystem/related_artifacts.json \
    --revstream1-data-dir ../revstream1/data_models \
    --write
```

## CLI entry point

`gnucash-cli --import-fincosys-sync <path> <accounts.gnucash>` (see
`Gnucash::import_fincosys_sync()` in `gnucash/gnucash-commands.cpp`, wired
into `gnucash-cli.cpp`'s option parsing) opens the given datafile, reads
the sync document at `<path>`, and calls both
`gnc_organizations_from_fincosys_json()` and
`gnc_transactions_from_syncfeed_json()` against it — each recognizes its
own schema by top-level key (`"organizations"` vs. `"transactions"`) and
returns `0` (not an error) when the other schema's document is passed in,
so a single flag works for either kind of sync document without the
caller having to know in advance which one they have. The book is saved
in place after import. This is the previously-missing wiring the note
below used to describe; the file `scripts/sync_fincosys_ecosystem.py`
produces can now be applied directly:

```bash
gnucash-cli --import-fincosys-sync data/fincosys_sync/gnucashm_ecosystem_sync.json \
    my-organizations.gnucash
```

**Not yet attempted**: a full CMake build/link of gnucashm's GnuCash
engine (it depends on guile, gtk+-3.0, webkit2gtk, libxml++, and boost
dev packages not present in every environment this change was authored
in) — the new code was written to reuse the exact same helpers, includes,
and session-open/save pattern already exercised by the neighbouring
`Gnucash::add_quotes()` command in the same file, but has not been
compiled end-to-end. Build and exercise it (`gnucash-cli
--import-fincosys-sync ... file.gnucash`) in an environment with the full
GnuCash toolchain before relying on it for a real import.

## Why helix's contribution is not treated as financial data

`fincosys/helix` is a fork of an unrelated generic AI-agent research-loop
tool. It holds no ledger data; the `ecosystem/related_artifacts.json`
manifest it publishes is hand-authored/self-reported, with no code in that
repo that computes or verifies the row/object counts it claims.
`fincosys-atomspace-builder`'s `loaders/helix.py` already accounts for this:
every node it produces from that manifest is tagged
`verification_status: "unverified_self_reported"` with a low-confidence
truth value, and the manifest's raw numbers are namespaced under
`helix_self_reported` rather than merged into verified attributes. This
script inherits that behavior unchanged — do not strip the tagging when
consuming its output, and do not cite `helix_self_reported` figures as
verified case evidence.
