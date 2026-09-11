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

import argparse
import json
import os
import sys
from collections import OrderedDict

SCHEMA = "fincosys-commerce-sync/v1"

# Components must reconcile to the document total within half a minor unit.
# Commerce sources round tax per line, so an exact equality test would
# reject records that are correct as issued.
RECONCILE_TOLERANCE = 0.005

#: record_type values that represent one bookable document.
BOOKABLE_RECORD_TYPES = ("sales_order", "sales_invoice")

#: record_type values that restate bookable records in aggregate. Booking
#: these as well would double-count revenue.
AGGREGATE_RECORD_TYPES = ("sales_period", "product_sales_summary")

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


def account_code(entity_code, suffix):
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


def build_accounts(entity_code, currency):
    """The four accounts a commerce document books against."""
    return [
        {
            "code": account_code(entity_code, suffix),
            "name": name,
            "parent_code": None,
            "account_type": account_type,
            "entity_code": entity_code,
            "currency": currency,
            "description": description,
        }
        for suffix, name, account_type, description in ACCOUNT_SPECS
    ]


def _splits_for(entity_code, amounts, tax_basis=DEFAULT_TAX_BASIS):
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
        "account_code": account_code(entity_code, "AR"),
        "amount": total,
        "memo": "",
    }]
    for value, suffix in ((revenue, "REVENUE"), (shipping, "SHIPPING"), (tax, "TAX")):
        if value:
            splits.append({
                "account_code": account_code(entity_code, suffix),
                "amount": -value,
                "memo": "",
            })

    return splits, total, components, revenue


def convert(document, source_path=""):
    """Convert one commerce document into (accounts, transactions, report).

    The returned shapes match sync_fincosys.py's ``build_plan`` inputs, so
    the resulting plan can be validated and applied by the existing
    machinery without it knowing commerce records exist.
    """
    entity_code = document["entity"]["code"]
    source = document.get("source", "unknown")
    window = document.get("window") or {}
    complete = bool(window.get("complete", False))

    transactions = []
    rejected = []
    skipped_aggregates = 0
    currencies = OrderedDict()
    tax_bases = OrderedDict((basis, 0) for basis in TAX_BASES)

    for record in document.get("records", []):
        record_type = record.get("record_type")

        if record_type in AGGREGATE_RECORD_TYPES:
            skipped_aggregates += 1
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

        splits, total, components, revenue = _splits_for(
            entity_code, record.get("amounts"), tax_basis
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

    currency = next(iter(currencies), "GBP")
    accounts = build_accounts(entity_code, currency) if transactions else []

    report = {
        "entity_code": entity_code,
        "source": source,
        "capture_status": "complete" if complete else "partial",
        "records_total": len(document.get("records", [])),
        "booked": len(transactions),
        "skipped_aggregates": skipped_aggregates,
        "rejected": rejected,
        "currencies": sorted(currencies),
        "tax_bases": {basis: count for basis, count in tax_bases.items() if count},
        "window": {k: window.get(k) for k in ("from", "to", "basis")},
    }
    if len(currencies) > 1:
        # Every account here is single-currency, so a mixed-currency
        # document would book foreign amounts into the wrong account.
        report["warnings"] = [
            "document mixes currencies {}; accounts were created in {} "
            "only".format(sorted(currencies), currency)
        ]

    return accounts, transactions, report


def convert_paths(paths):
    """Convert several commerce documents, merging their accounts."""
    accounts_by_code = OrderedDict()
    transactions = []
    reports = []

    for path in paths:
        document = load_commerce_document(path)
        accounts, txns, report = convert(document, source_path=path)
        report["path"] = path
        for account in accounts:
            accounts_by_code.setdefault(account["code"], account)
        transactions.extend(txns)
        reports.append(report)

    return list(accounts_by_code.values()), transactions, reports


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
        if report.get("tax_bases"):
            print("  tax basis        : {}".format(", ".join(
                "{} {}".format(count, basis)
                for basis, count in report["tax_bases"].items()
            )))
        print("  skipped aggregates: {}  (period/product totals restate the "
              "orders; booking them would double-count)".format(
                  report["skipped_aggregates"]))
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
        "documents", nargs="+",
        help="One or more fincosys-commerce-sync/v1 JSON documents.",
    )
    parser.add_argument(
        "--out",
        help="Write a sync feed (the shape sync_fincosys.py --feed reads) "
             "to this path. Without it, only the report is printed.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    try:
        accounts, transactions, reports = convert_paths(args.documents)
    except (OSError, ValueError) as exc:
        sys.stderr.write("error: {}\n".format(exc))
        return 1

    print_report(reports)

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
