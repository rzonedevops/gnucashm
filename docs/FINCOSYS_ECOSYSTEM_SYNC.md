# Fincosys Ecosystem Sync — Status

This documents how gnucashm connects to the wider fincosys financial
ecosystem: `fincosys/accospace`, `cogpy/fincosys`, `fincosys/helix`,
`cogpy/revstream1`, and `cogpy/ad-res-j7`.

> **Repository move.** The AtomSpace builder is now `fincosys/accospace`. It
> was `RegimA-Zone/fincosys-atomspace-builder`, and older sections below
> still name it that where they describe events from that time. The pip
> package and import name are unchanged --
> `fincosys-atomspace-builder` / `atomspace_builder` -- so only checkout
> paths and repository references move. `scripts/sync_fincosys_ecosystem.py`
> accepts a sibling checkout under either directory name.

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

**Update (2026-08-03)**: the `gnucash_ecosystem` preset now also enables
`include_transaction_index`, which loads fincosys's canonical, balance-
hash-verified `data/transaction_index.json` ledger (22K+ reconciled
transactions across all entities) via `TransactionIndexLoader` — additive
alongside the per-statement `include_transactions` extract loader already
in use, with a disjoint node-ID namespace (`TXI_<txid>` vs.
`TX_<account>_<stmt>_<index>`), so both can and do run together. No flag
change is needed on this side to pick it up; it flows through automatically
via `gnucash_ecosystem_config()`.

`.github/workflows/sync-fincosys-ecosystem.yml` runs that script on manual
dispatch: dry-run by default (build + validate + print counts only), or
`write: true` to persist a snapshot and open a draft PR for review. It
never runs on a schedule and never commits without an explicit run.

```bash
# Local usage, with sibling checkouts of the repos above:
python scripts/sync_fincosys_ecosystem.py \
    --atomspace-builder-dir ../accospace \
    --fincosys-data-dir ../fincosys/data \
    --helix-manifest ../helix/ecosystem/related_artifacts.json \
    --revstream1-data-dir ../revstream1/data_models \
    --write
```

## Cross-repo contract verification (this change)

The Python-bindings sync tool at
`bindings/python/example_scripts/fincosys_sync/` (`sync_fincosys.py`) has
its own, separate `--feed` input path for `fincosys-atomspace-builder`'s
`GnuCashSyncExporter` (`atomspace_builder/exporters/gnucash_exporter.py`)
— distinct from the `gnc_organizations_from_fincosys_json()` /
`gnc_transactions_from_syncfeed_json()` C++ bridge described above, and
already reachable without building the full engine (`--plan-only` is pure
Python; `--apply` needs only the SWIG Python bindings, not a full
menu/report integration). Until now, that `--feed` path and the exporter
it targets had never actually been run against each other — each side was
built and tested in its own repository against a shared *written* schema
description, with no fixture proving the two agree on the wire format.

`bindings/python/example_scripts/fincosys_sync/tests/fixtures/atomspace_builder_sync_feed.json`
closes that gap: it's a real document captured from
`generate_feed()`/`GnuCashSyncExporter` run against that repo's own
`tests/test_gnucash_exporter.py` fixture (provenance and regeneration
instructions in `tests/fixtures/README.md` alongside it), and
`tests/test_atomspace_builder_feed.py` feeds it through `load_feed()` +
`build_plan()` and asserts a clean result. This does not change the
C++-bridge status below — it verifies the independent Python `--feed`
path, which is the one with the shortest path to actually consuming real
exporter output today.

## Sync workflow status (2026-08-12)

Both existing runs of `.github/workflows/sync-fincosys-ecosystem.yml`
(2026-07-23, IDs 29995696001 and 30003177851 -- the latter *after* the
token-fallback fixes in PRs #28/#29) fail at the same step, "Checkout
fincosys-atomspace-builder", with the same error:

```
Retrieving the default branch name
Not Found - https://docs.github.com/rest/repos/repos#get-a-repository
```

This is not the bug those two PRs fixed (a missing `github.token` fallback
when `ECOSYSTEM_SYNC_TOKEN` isn't set at all) -- the fallback logic itself
now works correctly; the token it falls back to just doesn't have read
access to `RegimA-Zone/fincosys-atomspace-builder`, a private cross-org
repo. `github.token` (the default `GITHUB_TOKEN`) is scoped to the
repository the workflow runs in and cannot read a different org's private
repo no matter how the fallback is wired -- this requires an actual
`ECOSYSTEM_SYNC_TOKEN` secret (a PAT with read access to `RegimA-Zone`)
configured in this repo's settings, which does not appear to be set. The
workflow's own inline comment already says this correctly ("Sibling repos
are private and cross-repo -- this requires a personal access token with
read access to them, stored as `ECOSYSTEM_SYNC_TOKEN`"); this note just
confirms, from an actual failed run, that the described prerequisite is
the live blocker, not a hypothetical one. No further workflow-YAML change
is being attempted here: two prior sessions already iterated on the token
fallback logic itself and it is not the remaining problem, so a third
YAML edit without the ability to test it against a real secret would be
guessing, not a fix.

**Follow-up (owner action)**: create a PAT with read access to
`fincosys/accospace` (and, since the same token is
reused for all four cross-org checkouts, ideally also `cogpy/fincosys`,
`fincosys/helix`, `cogpy/revstream1`) and store it as the
`ECOSYSTEM_SYNC_TOKEN` secret in this repository. After that, re-run the
workflow with `write: false` first (dry run) to confirm the checkout and
build succeed before ever setting `write: true`.

## Snapshot freshness (2026-08-17)

The committed `data/fincosys_sync/gnucashm_ecosystem_sync.json` /
`gnucashm_ecosystem_atomspace.json` had not been regenerated since
2026-07-23 — three weeks stale, and from *before*
`fincosys-atomspace-builder`'s 2026-08-03 `TransactionIndexLoader` change
landed (see "What this change adds" above). Despite that section's text
already describing the transaction-index merge as automatic, the checked-in
snapshot never actually reflected it: it held 379 nodes / 21 organizations,
none of them transaction-index-derived.

Since the required sibling checkouts (`fincosys-atomspace-builder`,
`fincosys`, `helix`, `revstream1`) are all available locally in this
environment, this refresh ran the documented local-usage command directly
(no `ECOSYSTEM_SYNC_TOKEN` needed — that secret only gates the GitHub
Actions workflow's cross-org checkout step, not a local run against
existing sibling working copies) and re-committed the output. The new
snapshot: **24,035 nodes / 5,348 edges / 5 inferred rules**, 777
organizations (21 fincosys entities + counterparty pseudo-organizations
newly surfaced by the transaction-index merge, prefixed `CP_`), picking up
`cogpy/fincosys`'s 2026-08-12+ `MASTER_ACCOUNTS.json` refinement-tooling
changes and `cogpy/revstream1`'s subsequent case-evidence-model sync. This
is a data refresh only — no loader/exporter/bridge code changed.

## Builder repository moved to `fincosys/accospace` (2026-09-06)

The AtomSpace builder that `.github/workflows/sync-fincosys-ecosystem.yml`
checks out has moved from `RegimA-Zone/fincosys-atomspace-builder` to
`fincosys/accospace`. The workflow, `scripts/sync_fincosys_ecosystem.py` and
the docs here now target the new location; the script also still accepts a
sibling checkout named `fincosys-atomspace-builder`, so an existing local
clone keeps working.

**This does not, on its own, unblock the workflow.** The failure recorded
above is a token-scope problem, and it stays one: gnucashm lives in
`rzonedevops`, so `fincosys/accospace` is still a private cross-org checkout
that `github.token` cannot read. What changes is only *which* org the
`ECOSYSTEM_SYNC_TOKEN` PAT needs read access to -- `fincosys` rather than
`RegimA-Zone`. Since `fincosys/helix` was already on that list, a PAT scoped
to the `fincosys` org now covers two of the four cross-org checkouts instead
of one.

The workflow has not been re-run as part of this change: without the secret
configured it would fail at the same step for the same reason, and a run
that cannot succeed proves nothing.

## What is still missing (follow-up)

**Superseded — see "CLI entry point" below.** This section originally said
`gnc_organizations_from_fincosys_json()` was reachable only from the gtest
suite, with no CLI/Scheme/menu wiring. That gap was closed and verified
end-to-end in a later change (`gnucash-cli --import-fincosys-sync`, see
below); this note is kept only so the history of the gap is visible, not as
a current status.

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

**Verified end-to-end (2026-07-23)**: a full CMake build of `gnucash-cli`
(and the narrower `test-fincosys-sync` gtest target) now succeeds with no
compile fixes needed (`-DWITH_AQBANKING=OFF -DWITH_OFX=OFF -DWITH_SQL=OFF`
for optional subsystems whose dev packages weren't installed; none touch
this bridge). `gtest-fincosys-sync` passes all 25 cases.

Exercising a real import (`gnucash-cli --import-fincosys-sync
data/fincosys_sync/gnucashm_ecosystem_sync.json <book>`) surfaced and fixed
one genuine runtime bug: `gnc_organizations_from_fincosys_json()` created
each imported `Account` via `xaccMallocAccount()` but never attached it to
the book's root account tree via `gnc_account_append_child()`. The XML/SQL
backends discover accounts to persist by walking from the root account, so
those accounts were silently dropped on `qof_session_save()` even though
`gncOrganizationAddEntity()` had already linked them to their owning
organization -- confirmed by inspecting the saved/reloaded book before and
after the fix (accounts like "Aymac International" / `62012990132` now
correctly persist). The neighbouring `gnc_transactions_from_syncfeed_json()`
in the same file already did this correctly, which is what made the
omission obvious. Rebuilt and reran the gtest suite after the fix -- still
25/25.

**Known remaining gap -- closed and verified (2026-07-25)**: `GncOrganization`
now has its own XML backend module,
`libgnucash/backend/xml/gnc-organization-xml-v2.{h,cpp}`, built following the
exact `gnc-vendor-xml-v2.cpp` / `gnc-customer-xml-v2.cpp` pattern (dom-tree
writer, sixtp-based parser, `gnc_organization_xml_initialize()` registered
from `business_core_xml_init()` in `gnc-backend-xml.cpp`, source wired into
`libgnucash/backend/xml/CMakeLists.txt`). It persists id, name, notes
(carrying the round-tripped `evidence_refs`/`legal_categories`), address,
currency, the active flag, and the GUIDs of member entities (written with a
`qof-type` attribute per entity so they can be resolved back through
`qof_book_get_collection()`/`qof_collection_lookup_entity()` on load,
without assuming every member is an `Account` -- though that is the only
entity type any current producer of this data, `gnc-fincosys-sync.cpp`,
ever links).

This also required two small, necessary dependencies that were missing
before this change, not scope creep: (1) `gncOrganizationRegister()` was
never called anywhere in the engine (`libgnucash/engine/cashobjects.cpp`'s
`business_core_init()` registers every other business object but had no
call for organizations) -- without it, `qof_object_foreach()` cannot find
the `gncOrganization` QOF type at all, so the new writer's `get_count`/
`write` functions (which follow the vendor pattern of using
`qof_object_foreach_sorted()`) would have silently persisted zero
organizations even though the module was registered; this is now fixed by
adding the one missing `gncOrganizationRegister ();` call, mirroring every
sibling type. (2) a `gncOrganizationSetGUID` macro was added to
`gncOrganizationP.h`, mirroring the identical macro every other business
object's private header already defines, needed for the parser's
guid-handler to attach a GUID read from a file to a freshly-created
in-memory organization before it's committed.

**Build + test verification actually performed**: configured and built with
`cmake -DWITH_AQBANKING=OFF -DWITH_OFX=OFF -DWITH_SQL=OFF -DWITH_GNUCASH=OFF`
(the last flag additionally needed this session because `webkit2gtk-4.1`'s
dev package isn't installed in this environment and `WITH_GNUCASH` is what
gates that dependency; it only excludes the `gnucash/` GUI subdirectory --
`libgnucash`, its XML backend, and all of `libgnucash/*/test/` still build
and run normally). A full `make -j4` of everything under that configuration
completed with no errors. The new round-trip test,
`libgnucash/backend/xml/test/gtest-organization-xml-round-trip.cpp`
(registered in that directory's `CMakeLists.txt` as
`test-organization-xml-round-trip`), creates a `GncOrganization` with
evidence/legal-category-bearing notes (in the same tagged-line format
`gnc-fincosys-sync.cpp` writes), an address, a ZAR currency, and one member
`Account`; saves the book through a real `QofSession` to a temp XML file;
reloads it into a completely fresh `QofBook`/`QofSession`; and asserts the
organization's id, name, notes, guid, currency, address fields, and member
entity (by guid and name) all survived -- **actually run, 1/1 passed**:

```
[ RUN      ] OrganizationXmlRoundTrip.SurvivesSaveAndReload
[       OK ] OrganizationXmlRoundTrip.SurvivesSaveAndReload (7 ms)
[  PASSED  ] 1 test.
```

Also re-ran (not just rebuilt) the pre-existing suites this change touches,
all still green, no regressions: `test-fincosys-sync` 25/25, `test-vendor`
28/28, `test-customer` 34/34, `test-address` 27/27, `test-business` (all
pass, no failure output), `test-xml-account` 42/42, `test-xml-commodity`
40/40. `test-qof-multi-entity` -- previously documented above
(`ORGANIZATION_ENHANCEMENTS.md`) as 11/13 pre-existing failures on a clean
checkout of this branch -- now passes **13/13**; the `gncOrganizationRegister()`
fix above is the most likely explanation (those tests exercise
`QofMultiEntityCollection` behaviour over `GncOrganization` that depends on
the type being registered with QOF), but that was not independently
isolated/bisected in this session, so treat it as an observed side effect
worth a confirming look, not a claimed fix.

Not built or run this session: `gnucash-cli` itself and the full GUI/GTK
target (blocked by the same missing `webkit2gtk-4.1` dev package noted
above) -- unlike the 2026-07-23 verification of the account-persistence
fix, this change was verified at the `libgnucash` engine/XML-backend layer
via the gtest suite above, not via a `gnucash-cli --import-fincosys-sync`
run. The writer/parser pair is exercised directly by the test, which is the
same layer `gnucash-cli`'s import path ultimately calls into, but the
CLI-level, end-to-end path itself was not re-run for this specific change.

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


## Exporting back to accospace (2026-09-14)

Until now this repository could only be a *consumer* of the ecosystem sync
schema. `gnc_organizations_to_fincosys_json()` had existed since the bridge
was written, and `fincosys/accospace`'s `loaders/gnucashm.py` was written to
read what it produces -- as `gnucashm_export.json` -- but nothing in
gnucashm could actually call it outside `gtest-fincosys-sync.cpp`. The
documented `config.gnucashm_export_path` on the accospace side pointed at a
file no gnucashm command could write, so the loop
`accospace -> gnucashm -> accospace` was open at the return leg.

`gnucash-cli --export-fincosys-sync <out> <accounts.gnucash>` closes it
(`Gnucash::export_fincosys_sync()` in `gnucash/gnucash-commands.cpp`, wired
into `gnucash-cli.cpp`). It opens the datafile **read-only**, collects every
`GncOrganization` in the book's `GNC_ID_ORGANIZATION` collection, sorts them
by code, and writes the resulting `fincosys-ecosystem-sync/v1` document.

The sort matters: `qof_collection_foreach()` visits in hash order, so
without it two exports of an unchanged book would differ, and every sync
would look like a change to whatever is diffing them.

A book holding no organizations is reported and exits non-zero without
writing a file, rather than exiting 0 having written nothing -- otherwise a
pipeline would feed the previous run's stale file to the next stage as
though it were fresh.

```bash
# accospace -> gnucashm -> accospace, the whole loop
gnucash-cli --import-fincosys-sync data/fincosys_sync/gnucashm_ecosystem_sync.json book.gnucash
gnucash-cli --export-fincosys-sync gnucashm_export.json book.gnucash
```

**Verified end-to-end (2026-09-14)**, built with
`-DWITH_AQBANKING=OFF -DWITH_OFX=OFF -DWITH_SQL=OFF -DWITH_PYTHON=OFF`
(a full `ninja`, no compile fixes needed):

- Importing the committed `gnucashm_ecosystem_sync.json` into a fresh book
  gives `Imported 777 organization(s)`; exporting that book back gives
  `Exported 777 organization(s)`, a 160 KB `fincosys-ecosystem-sync/v1`
  document with `"source": "gnucashm"`.
- Two consecutive exports of the same book are **byte-identical**.
- Feeding that export to accospace's `GncMultiEntityLoader`
  (`include_gnucashm=True`, `gnucashm_export_path=...`) builds a hypergraph
  of 870 nodes / 83 edges, of which **831 carry `gnucashm_*` attributes** --
  the 777 organizations plus their 54 accounts. That is the cross-repo
  contract actually exercised rather than asserted.
- `test-fincosys-sync` still passes 25/25.

## Commerce records in more than one currency (2026-09-14)

Syncing RegimA @ Dr H Ltd's QuickBooks ledger (see
`fincosys/entity-regima-dr-h-uk`) produced the first commerce document
stating two currencies: 257 GBP invoices and 10 EUR ones. Every account
`bindings/python/example_scripts/fincosys_sync/commerce_import.py` creates
is single-currency, and it used to create them in whichever currency it saw
first and book everything into them, emitting a warning. So a EUR 15,869.82
invoice became GBP 15,869.82 in the receivable -- a wrong number that no
later reconciliation can tell apart from a real GBP balance, since the
transaction still balances.

It now rejects such a record instead, through the same rejection report that
already handles a record whose components don't reconcile: the same
principle -- surface it, don't paper over it. `--per-currency-accounts`
books them properly instead, into `COMM-<entity>-<CCY>-AR` and siblings. The
document's primary currency keeps its existing unscoped codes under both
modes, so a book already imported from a single-currency document is
unaffected and re-imports idempotently.

`tests/fixtures/commerce_quickbooks_rdh.json` is a real 19-record subset of
that ledger (GBP and EUR, standard-rated, zero-rated and discounted
invoices) and the suite checks both modes against it. 53 tests pass.

## Commerce records reach this repository through accospace (2026-09-22)

Until now `commerce_import.py` took commerce documents by path only, so the
ecosystem-sync path -- the one this document is about -- carried no sales at
all. accospace's `EcosystemSyncExporter` serialized `organizations` and
`atoms` and simply dropped every commerce record on the floor.

Worse, it did not drop all of them. The commerce loader creates a
counterparty as an `ENTITY` node and `organizations` was defined as "every
`ENTITY` node", so Shopify **retail customers were exported as fincosys group
organizations** and imported here by
`gnc_organizations_from_fincosys_json()` as `GncOrganization` records. One
month of RZL orders produces 78 of them.

accospace now excludes them and carries the records in a `commerce` section
instead (`fincosys/accospace`, `docs/COMMERCE_SYNC_SCHEMA.md`). This side
consumes it:

```bash
# accospace's hypergraph export, commerce section and all
python3 commerce_import.py ../accospace/out/ecosystem_sync.json --out feed.json

# or scan a directory of entity-repository checkouts
python3 commerce_import.py --repos-root ~/fincosys-repos --out feed.json

python3 sync_fincosys.py --feed feed.json --plan-only
```

### What `--repos-root` had to learn from the real corpus

Run against the 43 checked-out repositories it finds 15 record documents and
books 23,135 transactions for DRH, DRHW and RZL into a clean plan. Three
things were needed to get there, each from something the corpus actually
contains:

- **Report captures carry this schema too.** Four QuickBooks documents in
  `entity-regima-dr-h-uk` declare `fincosys-commerce-sync/v1` but hold a
  `report` or `items` block rather than `records` -- a balance sheet, a
  product/service list, two sales summaries. They are not this script's
  input and not errors; they are skipped and counted. A fifth,
  `2026-09-08_ap_aging_detail.json`, *does* carry records but spells its
  entity `entity.entity_code` instead of the schema's `entity.code`, so it
  is reported as unreadable and the run exits non-zero. One malformed file
  no longer stops the other fourteen.

- **The corpus keeps superseded captures on purpose.** RZL holds both the
  109-order Shopify window of 2026-09-06 and the 9,449-order history that
  replaced it, and both the 1,000-row and 13,051-row QuickBooks invoice
  exports. Feeding all of them produced 1,109 duplicate txids and an unclean
  plan. Copies that agree are collapsed -- complete capture over partial,
  then later `generated_at`, with more detail breaking the tie -- and the
  supersession is reported, not silent.

- **Differing detail is not differing figures.** The two QuickBooks exports
  decompose the same invoices differently: the older states total, tax and
  balance, the newer adds subtotal, shipping and discounts. All 1,000
  overlapping invoices agree on every stated figure. So the test for a
  conflict is the amount charged, not the whole decomposition; otherwise a
  thousand real invoices go unbooked over a disagreement that does not
  exist.

Where two captures *do* disagree on the amount charged, neither is booked
and both are named. Across the whole corpus exactly one record does:
`QBO_RZL_INVOICE_52168` (#9592) reads 276.76 with 0.76 outstanding in the
2026-09-06 export and 276.77 fully paid in the 2026-09-07 one. A penny of
rounding and a 76p payment in between -- immaterial, and still not resolved
here, because picking one would answer a question about the evidence
silently.

## Commerce records reach the ecosystem sync (this change)

Until now `scripts/sync_fincosys_ecosystem.py` produced ecosystem-sync
documents that carried **no QuickBooks or Shopify records at all**, however
many the entity repositories held.

The cause was a parameter nobody passed. accospace's `gnucash_ecosystem`
preset has accepted `commerce_record_paths` since the commerce schema
landed, and `include_commerce` is derived from it
(`include_commerce=bool(commerce_record_paths)`). This side called
`gnucash_ecosystem_config(data_dir=...)` and nothing else, so the flag was
always off. Both halves of the integration were built and documented — the
loader on accospace's side, `commerce_import.py` on this one — and the
producer in between never asked for them.

Two new flags close it:

```bash
# name documents, or a directory of them
python scripts/sync_fincosys_ecosystem.py --commerce-records ../entity-rzl/accounting

# or scan a directory of entity-repository checkouts
python scripts/sync_fincosys_ecosystem.py --entity-repos-root ~/fincosys-repos
```

Measured against the 43 checked-out repositories: **18,043 commerce nodes
from 18 record documents** (17,928 records plus 115 counterparty nodes),
against 0 before. The run reports the count, so
a sync that silently carried none again would be visible.

`.github/workflows/sync-fincosys-ecosystem.yml` gained an `entity_repos`
input that checks those repositories out and passes `--entity-repos-root`.
It defaults to the eight repositories known to hold a record document rather
than the whole corpus — checking out forty-odd repositories to read eight
files is not a reasonable default — and each clone is optional, because
access is granted per repository and a missing grant must not fail the sync.

### Ordering the documents is not cosmetic

`CommerceLoader` adds a record once (`if node_id not in hypergraph.nodes`)
and walks its configured paths in **sorted** order, so the first document to
carry a record wins and the winner is decided by filename.

That is the wrong authority. `entity-rzl` holds both a 1,000-row QuickBooks
invoice page and the 13,051-row export that superseded it, and
`2026-09-06_qbo_invoices.json` sorts *before* `2026-09-07_qbo_invoices.json`
— so every record the two share would have been taken from the partial
capture and pinned at the `partial_capture` confidence tier, in a build that
also held the complete one.

`select_commerce_documents()` therefore orders the paths by the rule the
booking side already uses to resolve a duplicate — a complete capture beats
a partial one, and among equals the later `generated_at` wins — which turns
the loader's first-wins into best-capture-wins without changing the loader.
Ordering by path would make the answer depend on where someone happened to
check the repositories out.

Discovery is `commerce_import.discover_documents`, reused rather than
rewritten, so "what counts as a commerce document" has one definition for
both the hypergraph path and the GnuCash-booking path.

### Two silent failures are now reported

`entity.code` is the schema's join key: the loader attaches each record to
the entity node the ecosystem-sync side already created from fincosys's
master data. Two things can go wrong with it and neither said anything.

**A code no entity node carries orphans every record in the document.**
`CommerceLoader._link` returns without adding the edge when a node it needs
is absent — deliberately, because inventing an entity would fabricate one —
so the record nodes are created and attach to nothing.

This is not hypothetical. `MASTER_ENTITIES.json` gives the entity at realm
`1366568670` the code **`RDH`**, listing `DRH` among its `qbo_aliases`. Two
documents in `entity-regima-dr-h-uk` declare the alias as `entity.code`.
Measured on a real build:

| | nodes | with an edge to their entity |
|---|---:|---:|
| `COMM_QBO_RDH_INVOICE_*` | 267 | **267** |
| `COMM_QBO_DRH_INVOICE_*` | 267 | **0** |

**One entity under two codes is counted twice.** Record ids embed the code,
so `QBO_RDH_INVOICE_12311` and `QBO_DRH_INVOICE_12311` are two different
record ids for one invoice. Neither the loader's node-id de-duplication nor
`commerce_import.resolve_duplicates` can see them as the same record, and
both are carried — which is why a corpus-wide plan reports `DRH 267` and
`RDH 267` as separate entities. 58 of the 267 also **disagree on the total**,
so this is an unresolved conflict that the de-duplication machinery is blind
to, not merely a duplicate.

`audit_entity_codes()` reports both. It does not adjudicate: which code is
canonical is a question for fincosys's master data, and where two captures
of one invoice disagree on the amount charged, choosing one would answer a
question about the evidence silently.

The second test keys on **the entity's own identity** — `entity.repository`,
or failing that the provider realm — and not on the record id alone. The
distinction is the whole difference between a finding and a false alarm: a
provider's document numbering is scoped to the provider account, so
`QBO_RZI_INVOICE_100` and `QBO_RZL_INVOICE_100` are two unrelated invoices
in two different QuickBooks companies. Keyed on the record id alone the audit
reported **903** collisions across this corpus, of which exactly one was
real. The realm needs both spellings, too: the corpus writes
`provider.realm_id` and `provider.realm` for the same field, and keying on
one of them splits an entity's documents into two anchors — which reads as
no collision at all, the failure the pass exists to catch.

`known_entity_codes()` returns `None` rather than an empty set when the
master data cannot be read, because "no codes are canonical" would report
every document in the corpus as unknown and bury the one real finding.

### A `report` block now wins over a `records` array

`entity-regima-dr-h-uk`'s `2026-09-08_ap_aging_detail.json` declares
`report: "ap_aging_detail"` **and** puts its 339 aging rows under `records`
— rows carrying `row_id`, `aging_bucket` and `days_past_due`, and no
`record_id`, `record_type` or `currency`. Read as a record document it is
simply malformed, and it made a corpus-wide `--repos-root` run exit
non-zero over one file.

It is not fixed at the source, on purpose: that capture is sealed by SHA-256
in its repository's own manifest, and editing the artifact to rewrite the
recorded hash would remove exactly the tamper-evidence the manifest exists
to provide (see that repository's
`integrations/sync-logs/2026-09-22_qbo_verification.md`, which records the
same decision). So `discover_documents` recognises it instead: a top-level
`report` or `items` block decides, even when `records` is also present.

The rule is narrow enough to be safe, and that was checked rather than
assumed — across the whole corpus exactly one document carries a `report` or
`items` key alongside records, and **no** genuine record document carries
either. With it, the corpus-wide booking run exits 0 and plans clean: 52
accounts, 16,393 transactions, 999 superseded captures collapsed, and the
one genuine figure conflict (`QBO_RZL_INVOICE_52168`) named and left
unbooked.
