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
