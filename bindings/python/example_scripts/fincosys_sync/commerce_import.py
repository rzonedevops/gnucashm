#!/usr/bin/env python3
# commerce_import.py -- turn fincosys commerce records (QuickBooks Online /
# Shopify) into the account + transaction shapes sync_fincosys.py already
# understands, so they can be planned and applied into a GnuCash book by the
# existing --plan-only / --apply machinery.
#
# Input is a "fincosys-commerce-sync/v1" document as written into the
# fincosys entity repositories (fincosys/entity-rzl, ...) under each repo's
# accounting/<source>/ canonical paths. The schema is specified in
# fincosys/accospace, docs/COMMERCE_SYNC_SCHEMA.md.
#
# Three ways in, all landing on the same converter
# ------------------------------------------------
# 1. A commerce document by path -- the original form.
# 2. A "fincosys-ecosystem-sync/v1" document carrying a `commerce` section.
#    That is accospace's export of a built hypergraph, and it is this
#    repository's actual integration surface with accospace: the same
#    document already carries organizations and cognitive atoms. Until the
#    section existed, commerce records could only reach here by someone
#    pointing this script at entity-repo files by hand, so the ecosystem
#    path silently carried no sales at all.
# 3. --repos-root <dir>, scanning a directory of entity-repo checkouts for
#    accounting/*/{raw-json,reports}/*.json. The corpus is 40-odd
#    repositories; naming each document on the command line is how a capture
#    gets left out without anyone noticing.
#
# A note on (2): the ecosystem-sync `commerce` section groups records by
# entity and carries counterparties once, referenced by id. It is expanded
# back into one commerce document per entity here, because the converter
# books per entity and an entity's records must not be split across two
# reports.
#
# Why this is a separate loader from sync_fincosys.py's --feed path
# ------------------------------------------------------------------
# The bank-statement corpus that --feed carries is single-sided: a statement
# line says money moved, with no counterpart in the source data, which is
# why that path invents Imbalance-<category> placeholder accounts to make
# each transaction balance.
#
# Commerce records are not like that. A sales order or invoice already
# carries its own decomposition -- net revenue, shipping, tax -- and those
# components sum to the document total. So this loader books real
# double-entry transactions against named revenue/tax/receivable accounts,
# with no placeholder and no plug:
#
#     Dr  Accounts Receivable      total
#         Cr  Sales Revenue            subtotal
#         Cr  Shipping Income          shipping
#         Cr  Tax Payable              tax
#
# That identity (total == subtotal + shipping + tax) is *checked*, per
# record, not assumed. A record that does not satisfy it is not booked and
# not silently plugged to zero -- it is returned in the rejection report
# with its discrepancy, because a commerce document whose components don't
# reconcile to its own total is a data problem to surface, not to paper
# over.
#
# VAT-inclusive records state the identity differently
# -----------------------------------------------------
# A record may carry "tax_basis": "inclusive", meaning the tax is contained
# in the subtotal rather than added to it, so the identity it satisfies is
# total == subtotal + shipping. The RegimA Zone store priced this way until
# 2018. Such a record books revenue net of the contained tax:
#
#     Dr  Accounts Receivable      total
#         Cr  Sales Revenue            subtotal - tax
#         Cr  Shipping Income          shipping
#         Cr  Tax Payable              tax
#
# Treating an inclusive record as exclusive would reject it as unreconciled
# -- which is what happened when the store's full history first reached this
# importer, and is how the basis came to be recorded at all.
#
# Two malformed cases are rejected rather than booked. A record declaring
# any other basis is not read as exclusive: the two differ by the whole tax
# amount, so guessing is guessing at the ledger. And an inclusive record
# whose tax exceeds the subtotal containing it is mis-stated, not a sale
# with negative revenue -- booking it would send revenue down on a sale.
#
# Aggregate record types are deliberately not booked
# ---------------------------------------------------
# A commerce document also carries "sales_period" and
# "product_sales_summary" records. Those describe the *same* revenue as the
# order records, sliced by month and by product. Booking them alongside the
# orders would double- and triple-count every sale. They are skipped, with a
# count reported, so the skip is visible rather than looking like data loss.
#
# A record in a currency the accounts are not in is rejected
# -----------------------------------------------------------
# The four accounts below are single-currency, so a document carrying more
# than one currency has no single right answer. Booking a EUR invoice into a
# GBP receivable is not a rounding problem, it is a wrong number in the
# ledger -- EUR 15,869.82 becomes GBP 15,869.82 -- and no later
# reconciliation can tell it apart from a real GBP balance.
#
# So records outside the document's own primary currency (the first one its
# bookable records state) are rejected by default, like any other record
# that cannot be booked correctly. Pass --per-currency-accounts to book them
# instead into accounts scoped by currency -- COMM-<entity>-<CCY>-AR
# alongside COMM-<entity>-AR -- which is correct but changes the account
# codes a book is keyed on, so it is opt-in rather than the default.
#
# QuickBooks Online's RegimA @ Dr H Ltd ledger is the case in point: 257 GBP
# invoices and 10 EUR ones in the same export.

import argparse
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path

SCHEMA = "fincosys-commerce-sync/v1"

#: accospace's export of a built hypergraph. Carries a `commerce` section
#: since the commerce records stopped being exported as group organizations.
ECOSYSTEM_SCHEMA = "fincosys-ecosystem-sync/v1"

#: Where an entity repository keeps commerce documents, relative to its root.
#: Both are scanned because a capture's orders and its period/product reports
#: are written to different ones.
ENTITY_REPO_GLOBS = (
    "accounting/*/raw-json/*.json",
    "accounting/*/reports/*.json",
)

# Components must reconcile to the document total within half a minor unit.
# Commerce sources round tax per line, so an exact equality test would
# reject records that are correct as issued.
RECONCILE_TOLERANCE = 0.005

#: record_type values that represent one bookable document.
BOOKABLE_RECORD_TYPES = ("sales_order", "sales_invoice")

#: record_type values that restate bookable records in aggregate. Booking
#: these as well would double-count revenue.
AGGREGATE_RECORD_TYPES = ("sales_period", "product_sales_summary")

#: Recognized, and deliberately not booked, for a different reason than the
#: aggregates above: an estimate is a quote, not a sale. Booking one credits
#: revenue for a transaction that may never happen, and if it does happen the
#: invoice raised against it books the same revenue again. entity-rzl holds 38
#: of these. They were previously reported as an "unrecognized record_type",
#: which reads as a data problem in the capture rather than as this script
#: declining to book a quote.
NOT_BOOKABLE_RECORD_TYPES = ("sales_estimate",)

#: How a record may state its tax. See the header comment for the two
#: identities. A record that declares anything else is rejected rather than
#: guessed at: the two bases differ by the whole tax amount, so a wrong
#: guess is a wrong ledger.
TAX_BASES = ("exclusive", "inclusive")

#: Documents written before tax_basis existed omit it and were exclusive.
DEFAULT_TAX_BASIS = "exclusive"

# Per-entity account codes. Suffixes are stable; the entity code makes them
# unique across a multi-entity book, matching sync_fincosys.py's convention
# for Imbalance-<entity>-<category>.
ACCOUNT_SPECS = (
    ("AR", "Accounts Receivable", "ASSET",
     "Amounts invoiced to commerce customers and not yet settled"),
    ("REVENUE", "Sales Revenue", "INCOME",
     "Net sales revenue from commerce documents, after discounts"),
    ("SHIPPING", "Shipping Income", "INCOME",
     "Shipping and delivery charged to commerce customers"),
    ("TAX", "Tax Payable", "LIABILITY",
     "Sales tax / VAT charged on commerce documents"),
)


def account_code(entity_code, suffix, currency=None):
    """The account code one commerce component books to.

    ``currency`` is omitted for the document's primary currency, so a
    single-currency document produces exactly the codes it always has and an
    existing book keeps matching. It is included only for the additional
    currencies --per-currency-accounts admits, which have no prior codes to
    stay compatible with.
    """
    if currency:
        return "COMM-{}-{}-{}".format(entity_code, currency, suffix)
    return "COMM-{}-{}".format(entity_code, suffix)


def _amount(amounts, key):
    """Read one money field as a float, treating absent/null as zero.

    The schema carries money as decimal strings so nothing is lost in
    transit; arithmetic here matches sync_fincosys.py's float convention
    and its BALANCE_TOLERANCE.
    """
    value = (amounts or {}).get(key)
    if value in (None, ""):
        return 0.0
    return float(value)


def load_commerce_document(path):
    """Read and validate one commerce document.

    Returns the parsed document. Raises ValueError if it is not a
    fincosys-commerce-sync/v1 document or declares no entity code, since
    either would make every record below it unattributable.
    """
    with open(path, "r", encoding="utf-8") as fh:
        document = json.load(fh)

    schema = document.get("schema")
    if schema != SCHEMA:
        raise ValueError(
            "{}: expected schema {!r}, got {!r}".format(path, SCHEMA, schema)
        )

    entity_code = (document.get("entity") or {}).get("code")
    if not entity_code:
        raise ValueError("{}: document declares no entity.code".format(path))

    return document


def documents_from_ecosystem_sync(document, path=""):
    """Expand an ecosystem-sync `commerce` section into commerce documents.

    accospace groups the records by entity code and carries counterparties
    once, referenced from each record by id; this rebuilds one
    fincosys-commerce-sync/v1 document per entity so the converter sees
    exactly what it sees from a file.

    Capture status is per record there but per document here, and the two
    are reconciled the conservative way: if any record in an entity's group
    is a partial capture, the whole rebuilt document is marked incomplete.
    Marking it complete because most records were would state, of a set that
    contains a known-partial capture, that it is the entity's full ledger.
    """
    commerce = document.get("commerce") or {}
    records_by_entity = commerce.get("records_by_entity") or {}
    parties = {
        party.get("id"): party for party in commerce.get("counterparties") or []
    }

    documents = []
    for entity_code in sorted(records_by_entity):
        records = records_by_entity[entity_code] or []
        if not entity_code:
            raise ValueError(
                "{}: commerce.records_by_entity has an empty entity code; its "
                "{} record(s) cannot be attributed".format(path, len(records))
            )

        rebuilt = []
        sources = OrderedDict()
        complete = True
        for record in records:
            record = dict(record)
            if record.pop("capture_status", None) == "partial":
                complete = False
            source = record.get("source")
            if source:
                sources[source] = True
            party_id = record.pop("counterparty_ref", None)
            if party_id and party_id in parties:
                party = parties[party_id]
                record["counterparty"] = {
                    "external_id": party.get("external_id"),
                    "name": party.get("name"),
                    "country": party.get("country"),
                }
            rebuilt.append(record)

        documents.append({
            "schema": SCHEMA,
            "source": "+".join(sources) if sources else "unknown",
            "generated_at": document.get("generated_at"),
            "entity": {"code": entity_code},
            "window": {
                "from": None,
                "to": None,
                "basis": "ecosystem_sync",
                "complete": complete,
            },
            "records": rebuilt,
        })

    return documents


def load_documents(path):
    """Read one path into a list of (document, label) pairs.

    A commerce document yields one; an ecosystem-sync document yields one per
    entity carrying commerce records, labelled `<path>#<ENTITY>` so a report
    still names where it came from.
    """
    with open(path, "r", encoding="utf-8") as fh:
        document = json.load(fh)

    schema = document.get("schema")
    if schema == ECOSYSTEM_SCHEMA:
        return [
            (doc, "{}#{}".format(path, doc["entity"]["code"]))
            for doc in documents_from_ecosystem_sync(document, path)
        ]

    # Re-read through the validating loader so its error messages, which name
    # the file, stay the ones a caller sees.
    return [(load_commerce_document(path), path)]


def discover_documents(repos_root):
    """Find commerce documents across a directory of entity-repo checkouts.

    Returns ``(found, skipped)``. Every candidate is opened and read,
    because the canonical paths also hold raw provider exports, sync
    manifests and QuickBooks report captures; those are neither commerce
    record documents nor errors, so they are skipped and counted rather
    than reported as failures.
    """
    root = Path(repos_root)
    if not root.is_dir():
        raise ValueError("{}: not a directory".format(repos_root))

    found = []
    skipped = []
    for repo in sorted(p for p in root.iterdir() if p.is_dir()):
        for pattern in ENTITY_REPO_GLOBS:
            for candidate in sorted(repo.glob(pattern)):
                try:
                    with open(candidate, "r", encoding="utf-8") as fh:
                        document = json.load(fh)
                except (OSError, ValueError):
                    continue
                if not isinstance(document, dict):
                    continue
                schema = document.get("schema")
                if schema == ECOSYSTEM_SCHEMA:
                    found.append(str(candidate))
                elif schema == SCHEMA:
                    # A record document has records. Several QuickBooks
                    # report captures in the corpus carry this schema but
                    # hold a `report` or `items` block instead -- a balance
                    # sheet, an AP aging detail, a product/service list.
                    # They are not this script's input and not errors; a
                    # document that *does* carry records but is malformed
                    # still reaches the validator below and is reported.
                    if isinstance(document.get("records"), list):
                        found.append(str(candidate))
                    else:
                        skipped.append(str(candidate))
    return found, skipped


def build_accounts(entity_code, currency, scoped=False):
    """The four accounts a commerce document books against, in one currency.

    ``scoped`` puts the currency in the code and the name, for the secondary
    currencies of a multi-currency document; the primary currency's accounts
    keep the unscoped codes.
    """
    return [
        {
            "code": account_code(entity_code, suffix,
                                 currency if scoped else None),
            "name": "{} ({})".format(name, currency) if scoped else name,
            "parent_code": None,
            "account_type": account_type,
            "entity_code": entity_code,
            "currency": currency,
            "description": description,
        }
        for suffix, name, account_type, description in ACCOUNT_SPECS
    ]


def _splits_for(entity_code, amounts, tax_basis=DEFAULT_TAX_BASIS,
                currency_scope=None):
    """Return (splits, total, components, revenue) for one bookable record.

    ``components`` is the sum the record's own total must equal under its
    declared tax basis -- subtotal + shipping + tax when tax is added to the
    subtotal, subtotal + shipping when it is already contained in it.
    ``revenue`` is what actually reaches the revenue account, which is the
    subtotal less any tax contained in it.

    Zero-valued components are omitted rather than booked as empty splits;
    a GBP 0.00 shipping split carries no information and clutters every
    transaction in a book.

    ``tax_basis`` says how the record states tax. On the default
    ``"exclusive"`` basis tax is added to the subtotal. On the
    ``"inclusive"`` basis -- VAT-inclusive pricing, which the RegimA Zone
    store used until 2018 -- the tax is *contained in* the subtotal, so the
    identity is ``total == subtotal + shipping``. Revenue is then the
    subtotal net of that tax; booking the stated subtotal as revenue *and*
    the tax as a liability would credit more than the customer was charged.
    """
    inclusive = tax_basis == "inclusive"

    total = _amount(amounts, "total")
    subtotal = _amount(amounts, "subtotal")
    shipping = _amount(amounts, "shipping")
    tax = _amount(amounts, "tax")

    if not subtotal and total:
        # QuickBooks invoices state a total and its tax, but no subtotal.
        # The net is the residual, which is exactly the figure that would
        # otherwise be missing from revenue. On an inclusive record the tax
        # is not added on top, so it is not part of that residual.
        subtotal = total - shipping if inclusive else total - tax - shipping

    # The tax on an inclusive record is a slice of the subtotal, not an
    # addition to it, so revenue is the subtotal net of it. Booking the
    # stated subtotal *and* the tax would over-credit by the tax.
    revenue = subtotal - tax if inclusive else subtotal
    components = subtotal + shipping if inclusive else subtotal + shipping + tax

    splits = [{
        "account_code": account_code(entity_code, "AR", currency_scope),
        "amount": total,
        "memo": "",
    }]
    for value, suffix in ((revenue, "REVENUE"), (shipping, "SHIPPING"), (tax, "TAX")):
        if value:
            splits.append({
                "account_code": account_code(entity_code, suffix, currency_scope),
                "amount": -value,
                "memo": "",
            })

    return splits, total, components, revenue


def convert(document, source_path="", per_currency_accounts=False):
    """Convert one commerce document into (accounts, transactions, report).

    The returned shapes match sync_fincosys.py's ``build_plan`` inputs, so
    the resulting plan can be validated and applied by the existing
    machinery without it knowing commerce records exist.

    ``per_currency_accounts`` admits records outside the document's primary
    currency by booking them into currency-scoped accounts. Without it such
    records are rejected rather than booked into an account of the wrong
    currency -- see the header comment.
    """
    entity_code = document["entity"]["code"]
    source = document.get("source", "unknown")
    window = document.get("window") or {}
    complete = bool(window.get("complete", False))

    transactions = []
    rejected = []
    skipped_aggregates = 0
    skipped_not_bookable = 0
    currencies = OrderedDict()
    #: Only the currencies of records that were actually booked. Accounts
    #: are built from these, so a rejected foreign-currency record does not
    #: leave an empty account behind in the plan.
    booked_currencies = OrderedDict()
    tax_bases = OrderedDict((basis, 0) for basis in TAX_BASES)

    for record in document.get("records", []):
        record_type = record.get("record_type")

        if record_type in AGGREGATE_RECORD_TYPES:
            skipped_aggregates += 1
            continue
        if record_type in NOT_BOOKABLE_RECORD_TYPES:
            skipped_not_bookable += 1
            continue
        if record_type not in BOOKABLE_RECORD_TYPES:
            rejected.append({
                "record_id": record.get("record_id"),
                "reason": "unrecognized record_type {!r}".format(record_type),
            })
            continue

        record_id = record.get("record_id")
        if not record_id:
            rejected.append({
                "record_id": None,
                "reason": "record has no record_id, so it cannot be made "
                          "idempotent on re-import",
            })
            continue

        tax_basis = record.get("tax_basis") or DEFAULT_TAX_BASIS
        if tax_basis not in TAX_BASES:
            rejected.append({
                "record_id": record_id,
                "reason": "unrecognized tax_basis {!r}; expected one of "
                          "{}".format(tax_basis, ", ".join(TAX_BASES)),
            })
            continue

        currency = record.get("currency") or "GBP"
        currencies[currency] = True

        # The first currency a bookable record states is the document's
        # primary one, and keeps the unscoped account codes.
        primary_currency = next(iter(currencies))
        if currency != primary_currency:
            if not per_currency_accounts:
                rejected.append({
                    "record_id": record_id,
                    "reason": "record is in {} but the document's accounts are "
                              "in {}; booking it would put a {} amount in a {} "
                              "account. Re-run with --per-currency-accounts to "
                              "book it into {}-scoped accounts.".format(
                                  currency, primary_currency, currency,
                                  primary_currency, currency),
                    "currency": currency,
                    "primary_currency": primary_currency,
                })
                continue
            currency_scope = currency
        else:
            currency_scope = None

        splits, total, components, revenue = _splits_for(
            entity_code, record.get("amounts"), tax_basis, currency_scope
        )
        discrepancy = total - components
        if abs(discrepancy) > RECONCILE_TOLERANCE:
            rejected.append({
                "record_id": record_id,
                "reason": "components do not reconcile to the document total "
                          "on its {} tax basis".format(tax_basis),
                "tax_basis": tax_basis,
                "total": total,
                "components": components,
                "discrepancy": discrepancy,
            })
            continue

        if revenue < -RECONCILE_TOLERANCE:
            # Only reachable on an inclusive record whose tax exceeds the
            # subtotal containing it. That is not a sale with negative
            # revenue, it is a mis-stated record -- book it and the entity's
            # revenue goes down when it makes a sale.
            rejected.append({
                "record_id": record_id,
                "reason": "tax exceeds the tax-inclusive subtotal that "
                          "contains it, so revenue would book negative",
                "tax_basis": tax_basis,
                "revenue": revenue,
            })
            continue

        tax_bases[tax_basis] += 1
        booked_currencies[currency] = True

        counterparty = record.get("counterparty") or {}
        transactions.append({
            # Namespaced so a commerce document and a bank-statement feed
            # can never collide on txid, and so re-importing the same
            # document is a no-op (see "Idempotency" in the README).
            "txid": "COMMERCE:{}".format(record_id),
            "date": (record.get("issued_at") or "")[:10],
            "description": "{} {}".format(
                source, record.get("document_number") or record_id
            ).strip(),
            "currency": currency,
            "entity_code": entity_code,
            "splits": splits,
            "metadata": {
                "commerce_source": source,
                "record_type": record_type,
                # Kept on the transaction because the split amounts alone
                # no longer say which identity produced them: an inclusive
                # record's revenue split is its subtotal less the tax.
                "tax_basis": tax_basis,
                "external_id": record.get("external_id"),
                "document_number": record.get("document_number"),
                "financial_status": record.get("financial_status"),
                "counterparty_name": counterparty.get("name"),
                "counterparty_external_id": counterparty.get("external_id"),
                # Carried through so anything reading the book can tell a
                # complete capture from a partial one without going back to
                # the source document.
                "capture_status": "complete" if complete else "partial",
                "source_document": os.path.basename(source_path) if source_path else "",
            },
        })

    accounts = []
    for index, booked in enumerate(booked_currencies):
        accounts.extend(build_accounts(entity_code, booked, scoped=index > 0))

    report = {
        "entity_code": entity_code,
        "source": source,
        "capture_status": "complete" if complete else "partial",
        "records_total": len(document.get("records", [])),
        "booked": len(transactions),
        "skipped_aggregates": skipped_aggregates,
        "skipped_not_bookable": skipped_not_bookable,
        "rejected": rejected,
        "currencies": sorted(currencies),
        "booked_currencies": sorted(booked_currencies),
        "per_currency_accounts": bool(per_currency_accounts),
        "tax_bases": {basis: count for basis, count in tax_bases.items() if count},
        "window": {k: window.get(k) for k in ("from", "to", "basis")},
    }
    if len(currencies) > 1:
        # Every account is single-currency, so a mixed-currency document
        # either splits across per-currency accounts or leaves its foreign
        # records unbooked. Either way, say which happened.
        primary = next(iter(currencies))
        if per_currency_accounts:
            report["warnings"] = [
                "document mixes currencies {}; {} kept the unscoped account "
                "codes and the rest were booked into currency-scoped ones"
                .format(sorted(currencies), primary)
            ]
        else:
            report["warnings"] = [
                "document mixes currencies {}; only the {} records were "
                "booked. The rest are in the rejection list -- re-run with "
                "--per-currency-accounts to book them too."
                .format(sorted(currencies), primary)
            ]

    return accounts, transactions, report


def _splits_fingerprint(transaction):
    """What two copies of one record must agree on to be the same record.

    The test is the amount charged -- the debit to receivables -- and not the
    whole decomposition, because two captures of one invoice routinely
    decompose it differently while agreeing on every stated figure. RZL's
    QuickBooks invoices are captured both ways in this corpus: the
    2026-09-06 export states total, tax and balance only, and the 2026-09-07
    one adds subtotal, shipping and discounts. All 1,000 overlapping
    invoices agree on total, tax and balance; only the detail differs.

    So a difference in decomposition is a difference in how well the capture
    saw the invoice, which supersession settles. A difference in the amount
    charged is a disagreement about what happened, which it must not.
    """
    return round(
        sum(
            float(split.get("amount", 0))
            for split in transaction.get("splits", [])
            if float(split.get("amount", 0)) > 0
        ),
        2,
    )


def _supersession_rank(document, label):
    """Order two captures of the same record. Higher wins.

    A complete capture beats a partial one -- a window that covers the
    entity's history is a better authority than one that stopped short --
    and among equals the later `generated_at` wins. Ordering by path would
    make the answer depend on where someone happened to check the
    repositories out.
    """
    window = document.get("window") or {}
    return (
        1 if window.get("complete") else 0,
        document.get("generated_at") or "",
        label,
    )


def _detail_rank(transaction):
    """How finely a capture decomposed the record. More splits is more detail.

    Used only to break a tie between copies that agree on the amount charged:
    an invoice booked across revenue, shipping and tax is a better record of
    the same sale than one booked to a single account, whatever their dates.
    """
    return len(transaction.get("splits", []))


def resolve_duplicates(entries):
    """Collapse records captured more than once, and flag those that disagree.

    The corpus deliberately keeps dated captures side by side, so scanning it
    finds the same record twice: RZL's Shopify history holds the 109-order
    window of 2026-09-06 as well as the 9,449-order capture that superseded
    it, and its QuickBooks invoices likewise. Feeding both produces duplicate
    txids and an unclean plan.

    Two copies that agree are one record captured twice, and the better
    capture is kept. Two copies that *disagree* are a conflict, and neither
    is booked: picking one would answer a question about the evidence
    silently, which is the same rule the statement corpus follows when two
    extracts of one statement disagree.

    Returns ``(transactions, superseded, conflicts)``.
    """
    by_txid = OrderedDict()
    for transaction, rank, label in entries:
        by_txid.setdefault(transaction.get("txid"), []).append(
            (transaction, rank, label)
        )

    transactions = []
    superseded = []
    conflicts = []
    for txid, copies in by_txid.items():
        if len(copies) == 1:
            transactions.append(copies[0][0])
            continue

        fingerprints = {_splits_fingerprint(copy[0]) for copy in copies}
        if len(fingerprints) > 1:
            conflicts.append({
                "txid": txid,
                "sources": sorted(copy[2] for copy in copies),
            })
            continue

        winner = max(
            copies, key=lambda copy: (_detail_rank(copy[0]), copy[1])
        )
        transactions.append(winner[0])
        superseded.append({
            "txid": txid,
            "kept": winner[2],
            "dropped": sorted(
                copy[2] for copy in copies if copy[2] != winner[2]
            ),
        })

    return transactions, superseded, conflicts


def convert_paths(paths, keep_going=False, per_currency_accounts=False):
    """Convert several commerce documents, merging their accounts.

    With ``keep_going``, a document that cannot be read or validated is
    collected and the rest still convert -- one malformed file in a 40-repo
    corpus should not stop the other thirty-nine. The failures are returned
    so the caller can report them and exit non-zero; they are never
    swallowed.
    """
    accounts_by_code = OrderedDict()
    entries = []
    reports = []
    failures = []

    for path in paths:
        try:
            loaded = load_documents(path)
        except (OSError, ValueError) as exc:
            if not keep_going:
                raise
            failures.append(str(exc))
            continue

        for document, label in loaded:
            accounts, txns, report = convert(
                document, source_path=label,
                per_currency_accounts=per_currency_accounts,
            )
            report["path"] = label
            for account in accounts:
                accounts_by_code.setdefault(account["code"], account)
            rank = _supersession_rank(document, label)
            entries.extend((txn, rank, label) for txn in txns)
            reports.append(report)

    transactions, superseded, conflicts = resolve_duplicates(entries)

    return (
        list(accounts_by_code.values()),
        transactions,
        reports,
        failures,
        {"superseded": superseded, "conflicts": conflicts},
    )


def print_report(reports):
    print("fincosys commerce -> GnuCash import")
    print("=" * 52)
    for report in reports:
        print("\n{}".format(report.get("path", "")))
        print("  entity           : {}".format(report["entity_code"]))
        print("  source           : {} ({} capture)".format(
            report["source"], report["capture_status"]))
        window = report["window"]
        print("  window           : {} -> {} (by {})".format(
            window.get("from"), window.get("to"), window.get("basis")))
        print("  records          : {}".format(report["records_total"]))
        print("  booked           : {}".format(report["booked"]))
        if len(report["currencies"]) > 1:
            print("  currencies       : {} stated, booked in {}".format(
                ", ".join(report["currencies"]),
                ", ".join(report["booked_currencies"]) or "none"))
        if report.get("tax_bases"):
            print("  tax basis        : {}".format(", ".join(
                "{} {}".format(count, basis)
                for basis, count in report["tax_bases"].items()
            )))
        print("  skipped aggregates: {}  (period/product totals restate the "
              "orders; booking them would double-count)".format(
                  report["skipped_aggregates"]))
        if report.get("skipped_not_bookable"):
            print("  skipped quotes   : {}  (an estimate is not a sale; "
                  "booking it credits revenue for a transaction that may "
                  "never happen)".format(report["skipped_not_bookable"]))
        print("  rejected         : {}".format(len(report["rejected"])))
        for rejection in report["rejected"][:10]:
            print("      {}: {}".format(
                rejection.get("record_id"), rejection.get("reason")))
        if len(report["rejected"]) > 10:
            print("      ... and {} more".format(len(report["rejected"]) - 10))
        for warning in report.get("warnings", []):
            print("  WARNING          : {}".format(warning))
        if report["capture_status"] == "partial":
            print("  NOTE             : this capture is declared incomplete; "
                  "the booked transactions are not the entity's full "
                  "commerce ledger.")


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Convert fincosys commerce records (QuickBooks/Shopify) "
                    "into a GnuCash sync feed."
    )
    parser.add_argument(
        "documents", nargs="*",
        help="One or more fincosys-commerce-sync/v1 documents, or "
             "fincosys-ecosystem-sync/v1 documents carrying a commerce "
             "section (accospace's hypergraph export).",
    )
    parser.add_argument(
        "--repos-root",
        help="Directory of fincosys entity-repository checkouts to scan for "
             "commerce documents, instead of (or as well as) naming them. "
             "The corpus is 40-odd repositories and a capture named by hand "
             "is a capture that can be left out silently.",
    )
    parser.add_argument(
        "--out",
        help="Write a sync feed (the shape sync_fincosys.py --feed reads) "
             "to this path. Without it, only the report is printed.",
    )
    parser.add_argument(
        "--per-currency-accounts", action="store_true",
        help="Book records outside the document's primary currency into "
             "currency-scoped accounts (COMM-<entity>-<CCY>-AR) instead of "
             "rejecting them. The primary currency keeps its existing "
             "unscoped codes, so books already imported are unaffected.",
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    paths = list(args.documents)
    discovering = bool(args.repos_root)
    try:
        if discovering:
            discovered, skipped = discover_documents(args.repos_root)
            if not discovered:
                sys.stderr.write(
                    "error: no commerce documents under {}\n".format(
                        args.repos_root)
                )
                return 1
            print("Discovered {} commerce document(s) under {}".format(
                len(discovered), args.repos_root))
            if skipped:
                print("Skipped {} document(s) carrying the schema with no "
                      "records array (report captures, not record "
                      "documents)".format(len(skipped)))
            paths.extend(discovered)
        if not paths:
            parser.error("name at least one document, or pass --repos-root")

        accounts, transactions, reports, failures, duplicates = convert_paths(
            paths,
            keep_going=discovering,
            per_currency_accounts=args.per_currency_accounts,
        )
    except (OSError, ValueError) as exc:
        sys.stderr.write("error: {}\n".format(exc))
        return 1

    print_report(reports)

    superseded = duplicates["superseded"]
    conflicts = duplicates["conflicts"]
    if superseded:
        print("\n{} record(s) captured more than once; the better capture "
              "was kept".format(len(superseded)))
        for entry in superseded[:5]:
            print("    {}: kept {}".format(entry["txid"], entry["kept"]))
        if len(superseded) > 5:
            print("    ... and {} more".format(len(superseded) - 5))
    if conflicts:
        # Not booked, and not resolved here: two captures of one record whose
        # figures disagree is a question about the evidence, and picking one
        # would answer it silently.
        sys.stderr.write(
            "\n{} record(s) captured twice with DIFFERENT figures; none "
            "booked:\n".format(len(conflicts))
        )
        for entry in conflicts[:10]:
            sys.stderr.write("  {}: {}\n".format(
                entry["txid"], ", ".join(entry["sources"])))
        if len(conflicts) > 10:
            sys.stderr.write("  ... and {} more\n".format(len(conflicts) - 10))

    if failures:
        # Reported, never swallowed: a document claiming this schema that
        # cannot be read is a defect in whatever produced it.
        sys.stderr.write(
            "\n{} document(s) could not be read:\n".format(len(failures))
        )
        for failure in failures:
            sys.stderr.write("  {}\n".format(failure))

    if args.out:
        feed = {
            "schema_version": "1.0",
            "source": {"generator": "gnucashm commerce_import.py"},
            "accounts": accounts,
            "transactions": transactions,
        }
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(feed, fh, indent=2)
            fh.write("\n")
        print("\nWrote {} accounts and {} transactions to {}".format(
            len(accounts), len(transactions), args.out))
        print("Plan it with: python3 sync_fincosys.py --feed {} --plan-only"
              .format(args.out))

    return 1 if (failures or conflicts) else 0


if __name__ == "__main__":
    sys.exit(main())
