#!/usr/bin/env python3

# test_commerce_import.py -- covers commerce_import.py, which turns fincosys
# commerce records (QuickBooks Online / Shopify, schema
# fincosys-commerce-sync/v1) into the account/transaction shapes
# sync_fincosys.py already plans and applies.
#
# The last test in this file is a cross-repo contract test: it feeds a real
# document captured from the Shopify Admin API (checked in at
# tests/fixtures/commerce_shopify_rzl.json, provenance in
# tests/fixtures/README.md) through commerce_import.convert() and then
# through sync_fincosys.build_plan(), and asserts the resulting plan is
# clean -- i.e. that every booked transaction balances and references an
# account the same run created. Pure Python; no built GnuCash needed.

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import commerce_import as ci  # noqa: E402
import sync_fincosys as sf  # noqa: E402

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "commerce_shopify_rzl.json",
)

QBO_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "commerce_quickbooks_rdh.json",
)


def _order(**overrides):
    record = {
        "record_id": "SHOPIFY_RZL_ORDER_10318",
        "record_type": "sales_order",
        "external_id": "13317005050230",
        "document_number": "#10318",
        "issued_at": "2026-08-01T11:36:15Z",
        "financial_status": "PAID",
        "currency": "GBP",
        "counterparty": {"external_id": "755", "name": "Kat Buckley"},
        "amounts": {
            "subtotal": "329.5",
            "discounts": "44.0",
            "shipping": "17.05",
            "tax": "65.9",
            "total": "412.45",
        },
    }
    record.update(overrides)
    return record


def _document(records=None, complete=True, source="shopify"):
    return {
        "schema": "fincosys-commerce-sync/v1",
        "source": source,
        "generated_at": "2026-09-06T00:00:00Z",
        "entity": {"code": "RZL", "legal_name": "Regima Zone Ltd"},
        "window": {
            "from": "2026-08-01",
            "to": "2026-09-04",
            "basis": "created_at",
            "complete": complete,
        },
        "records": [_order()] if records is None else records,
    }


# -- accounts ---------------------------------------------------------------


def test_four_accounts_are_created_for_the_entity():
    accounts, _, _ = ci.convert(_document())

    codes = sorted(a["code"] for a in accounts)
    assert codes == [
        "COMM-RZL-AR",
        "COMM-RZL-REVENUE",
        "COMM-RZL-SHIPPING",
        "COMM-RZL-TAX",
    ]
    by_code = {a["code"]: a for a in accounts}
    assert by_code["COMM-RZL-AR"]["account_type"] == "ASSET"
    assert by_code["COMM-RZL-REVENUE"]["account_type"] == "INCOME"
    assert by_code["COMM-RZL-TAX"]["account_type"] == "LIABILITY"
    assert by_code["COMM-RZL-AR"]["currency"] == "GBP"


def test_no_accounts_are_created_when_nothing_books():
    accounts, transactions, _ = ci.convert(_document(records=[]))

    assert accounts == []
    assert transactions == []


def test_accounts_are_merged_across_documents(tmp_path):
    paths = []
    for name in ("a.json", "b.json"):
        path = tmp_path / name
        path.write_text(json.dumps(_document()))
        paths.append(str(path))

    accounts, transactions, reports, _, duplicates = ci.convert_paths(paths)

    assert len(accounts) == 4
    assert len(reports) == 2
    # The same record captured in two documents books once. Booking it twice
    # double-counts the sale, and sync_fincosys would reject the plan for
    # duplicate txids anyway -- the two copies agree, so the better capture
    # is kept and the supersession is reported rather than being silent.
    assert len(transactions) == 1
    assert len(duplicates["superseded"]) == 1
    assert duplicates["conflicts"] == []


# -- booking ----------------------------------------------------------------


def test_an_order_books_as_balanced_double_entry():
    _, transactions, _ = ci.convert(_document())

    txn = transactions[0]
    assert txn["txid"] == "COMMERCE:SHOPIFY_RZL_ORDER_10318"
    assert txn["date"] == "2026-08-01"
    assert txn["entity_code"] == "RZL"
    assert abs(sum(s["amount"] for s in txn["splits"])) < sf.BALANCE_TOLERANCE

    by_account = {s["account_code"]: s["amount"] for s in txn["splits"]}
    assert by_account["COMM-RZL-AR"] == pytest.approx(412.45)
    assert by_account["COMM-RZL-REVENUE"] == pytest.approx(-329.5)
    assert by_account["COMM-RZL-SHIPPING"] == pytest.approx(-17.05)
    assert by_account["COMM-RZL-TAX"] == pytest.approx(-65.9)


def test_zero_components_are_not_booked_as_empty_splits():
    order = _order(amounts={"subtotal": "100.0", "shipping": "0.0",
                            "tax": "0.0", "total": "100.0"})
    _, transactions, _ = ci.convert(_document([order]))

    codes = {s["account_code"] for s in transactions[0]["splits"]}
    assert codes == {"COMM-RZL-AR", "COMM-RZL-REVENUE"}


def test_a_quickbooks_invoice_derives_its_net_from_total_less_tax():
    """QBO states a total and its tax but no subtotal; the net is the
    residual, which is exactly the figure revenue would otherwise miss."""
    invoice = {
        "record_id": "QBO_RZL_INVOICE_27545",
        "record_type": "sales_invoice",
        "document_number": "#2315",
        "issued_at": "2020-03-25",
        "currency": "GBP",
        "amounts": {"total": "120.00", "tax": "20.00", "balance": "0.00"},
    }
    _, transactions, report = ci.convert(_document([invoice], source="quickbooks"))

    by_account = {s["account_code"]: s["amount"] for s in transactions[0]["splits"]}
    assert by_account["COMM-RZL-AR"] == pytest.approx(120.0)
    assert by_account["COMM-RZL-REVENUE"] == pytest.approx(-100.0)
    assert by_account["COMM-RZL-TAX"] == pytest.approx(-20.0)
    assert report["rejected"] == []


def test_metadata_carries_provenance_and_counterparty():
    _, transactions, _ = ci.convert(_document(), source_path="/x/orders.json")

    meta = transactions[0]["metadata"]
    assert meta["commerce_source"] == "shopify"
    assert meta["document_number"] == "#10318"
    assert meta["counterparty_name"] == "Kat Buckley"
    assert meta["capture_status"] == "complete"
    assert meta["source_document"] == "orders.json"


def test_partial_capture_is_marked_on_every_transaction():
    _, transactions, report = ci.convert(_document(complete=False))

    assert report["capture_status"] == "partial"
    assert transactions[0]["metadata"]["capture_status"] == "partial"


def test_txids_are_namespaced_so_they_cannot_collide_with_bank_feeds():
    _, transactions, _ = ci.convert(_document())

    assert transactions[0]["txid"].startswith("COMMERCE:")


# -- tax basis --------------------------------------------------------------


def _inclusive_order(**overrides):
    """A VAT-inclusive order: the tax sits inside the stated subtotal.

    120.00 of goods containing 20.00 of VAT, plus 10.00 of shipping, is a
    132.00 (not 152.00) document. Modelled on the pre-2018 RegimA Zone
    orders that accospace's Shopify normalizer stamps as inclusive.
    """
    return _order(
        tax_basis="inclusive",
        amounts={
            "subtotal": "120.00",
            "shipping": "12.00",
            "tax": "20.00",
            "total": "132.00",
        },
        **overrides
    )


def test_an_inclusive_record_books_revenue_net_of_the_tax_it_contains():
    _, transactions, report = ci.convert(_document([_inclusive_order()]))

    assert report["rejected"] == []
    txn = transactions[0]
    by_account = {s["account_code"]: s["amount"] for s in txn["splits"]}
    assert by_account["COMM-RZL-AR"] == pytest.approx(132.0)
    # 120.00 subtotal less the 20.00 VAT it contains.
    assert by_account["COMM-RZL-REVENUE"] == pytest.approx(-100.0)
    assert by_account["COMM-RZL-SHIPPING"] == pytest.approx(-12.0)
    assert by_account["COMM-RZL-TAX"] == pytest.approx(-20.0)
    assert abs(sum(s["amount"] for s in txn["splits"])) < sf.BALANCE_TOLERANCE


def test_an_inclusive_record_is_not_rejected_by_the_exclusive_identity():
    """The regression this basis support exists for.

    Read as exclusive, an inclusive record misses its own total by exactly
    the contained tax and is rejected -- the order goes missing from the
    book rather than booking wrong.
    """
    _, transactions, report = ci.convert(_document([_inclusive_order()]))

    assert len(transactions) == 1
    assert report["booked"] == 1


def test_an_inclusive_invoice_without_a_subtotal_derives_it_from_the_total():
    """QBO states no subtotal; on an inclusive record the tax is not added
    on top, so it is not deducted to get the residual either."""
    invoice = {
        "record_id": "QBO_RZL_INVOICE_991",
        "record_type": "sales_invoice",
        "issued_at": "2017-06-01",
        "currency": "GBP",
        "tax_basis": "inclusive",
        "amounts": {"total": "120.00", "tax": "20.00", "balance": "0.00"},
    }
    _, transactions, report = ci.convert(_document([invoice], source="quickbooks"))

    assert report["rejected"] == []
    by_account = {s["account_code"]: s["amount"] for s in transactions[0]["splits"]}
    assert by_account["COMM-RZL-AR"] == pytest.approx(120.0)
    assert by_account["COMM-RZL-REVENUE"] == pytest.approx(-100.0)
    assert by_account["COMM-RZL-TAX"] == pytest.approx(-20.0)


def test_an_absent_tax_basis_is_read_as_exclusive():
    """Documents written before the field existed omit it, and were
    exclusive. Nothing about how they book may change."""
    _, transactions, report = ci.convert(_document())

    assert "tax_basis" not in _order()
    assert report["tax_bases"] == {"exclusive": 1}
    by_account = {s["account_code"]: s["amount"] for s in transactions[0]["splits"]}
    assert by_account["COMM-RZL-REVENUE"] == pytest.approx(-329.5)


def test_the_report_counts_records_by_tax_basis():
    inclusive = _inclusive_order(record_id="SHOPIFY_RZL_ORDER_9001")
    _, _, report = ci.convert(_document([_order(), inclusive]))

    assert report["tax_bases"] == {"exclusive": 1, "inclusive": 1}


def test_metadata_carries_the_tax_basis_a_transaction_was_booked_on():
    _, transactions, _ = ci.convert(_document([_inclusive_order()]))

    assert transactions[0]["metadata"]["tax_basis"] == "inclusive"


def test_an_unrecognized_tax_basis_is_rejected_not_guessed():
    """The two bases differ by the whole tax, so a guess is a wrong ledger."""
    order = _order(tax_basis="net_of_vat")
    _, transactions, report = ci.convert(_document([order]))

    assert transactions == []
    assert "unrecognized tax_basis" in report["rejected"][0]["reason"]


def test_an_inclusive_record_whose_tax_exceeds_its_subtotal_is_rejected():
    order = _order(tax_basis="inclusive", amounts={
        "subtotal": "20.00", "shipping": "0.0", "tax": "50.00", "total": "20.00",
    })
    _, transactions, report = ci.convert(_document([order]))

    assert transactions == []
    assert "negative" in report["rejected"][0]["reason"]


def test_an_exclusive_record_mislabelled_inclusive_still_has_to_reconcile():
    """Mislabelling is not silently absorbed: the stated total no longer
    matches the components under the declared basis, so it is reported."""
    order = _order(tax_basis="inclusive")  # totals state tax on top
    _, transactions, report = ci.convert(_document([order]))

    assert transactions == []
    rejection = report["rejected"][0]
    assert "inclusive tax basis" in rejection["reason"]
    assert rejection["discrepancy"] == pytest.approx(65.9)


# -- what must not be booked ------------------------------------------------


def test_aggregate_records_are_skipped_not_booked():
    """Period and product totals restate the same revenue as the orders.

    Booking them alongside would double- and triple-count every sale.
    """
    records = [
        _order(),
        {
            "record_id": "SHOPIFY_RZL_PERIOD_2026-08",
            "record_type": "sales_period",
            "period": "2026-08",
            "currency": "GBP",
            "amounts": {"total_sales": "27788.24"},
        },
        {
            "record_id": "SHOPIFY_RZL_PRODUCT_X",
            "record_type": "product_sales_summary",
            "product_title": "Derma Zest - 140ml",
            "currency": "GBP",
            "amounts": {"gross_sales": "27334.92"},
        },
    ]
    _, transactions, report = ci.convert(_document(records))

    assert len(transactions) == 1
    assert report["booked"] == 1
    assert report["skipped_aggregates"] == 2
    assert report["rejected"] == []


def test_a_record_whose_components_do_not_reconcile_is_rejected_not_plugged():
    broken = _order(amounts={
        "subtotal": "100.0", "shipping": "0.0", "tax": "10.0", "total": "999.0",
    })
    _, transactions, report = ci.convert(_document([broken]))

    assert transactions == []
    assert len(report["rejected"]) == 1
    rejection = report["rejected"][0]
    assert "reconcile" in rejection["reason"]
    assert rejection["discrepancy"] == pytest.approx(889.0)


def _splits_by_suffix(transaction):
    return {
        split["account_code"].rsplit("-", 1)[-1]: split["amount"]
        for split in transaction["splits"]
    }


def test_a_vat_inclusive_record_is_booked_not_rejected():
    """#1004's shape: total == subtotal + shipping, VAT inside the subtotal."""
    order = _order(
        record_id="SHOPIFY_RZL_ORDER_1004",
        tax_basis="inclusive",
        amounts={
            "subtotal": "22.42", "shipping": "10.0", "tax": "1.24", "total": "32.42",
        },
    )
    _, transactions, report = ci.convert(_document([order]))

    assert report["rejected"] == []
    assert len(transactions) == 1


def test_a_vat_inclusive_record_books_revenue_net_of_the_contained_tax():
    order = _order(
        tax_basis="inclusive",
        amounts={
            "subtotal": "22.42", "shipping": "10.0", "tax": "1.24", "total": "32.42",
        },
    )
    _, transactions, _ = ci.convert(_document([order]))
    splits = _splits_by_suffix(transactions[0])

    # 22.42 stated, of which 1.24 is VAT -> 21.18 of revenue.
    assert splits["REVENUE"] == pytest.approx(-21.18)
    assert splits["TAX"] == pytest.approx(-1.24)
    assert splits["SHIPPING"] == pytest.approx(-10.0)
    assert splits["AR"] == pytest.approx(32.42)


def test_a_vat_inclusive_record_still_balances():
    order = _order(
        tax_basis="inclusive",
        amounts={
            "subtotal": "22.42", "shipping": "10.0", "tax": "1.24", "total": "32.42",
        },
    )
    _, transactions, _ = ci.convert(_document([order]))

    assert sum(split["amount"] for split in transactions[0]["splits"]) == pytest.approx(0.0)


def test_an_absent_tax_basis_is_treated_as_exclusive():
    """Every document written before the basis existed omits the field."""
    order = _order(amounts={
        "subtotal": "100.0", "shipping": "0.0", "tax": "20.0", "total": "120.0",
    })
    order.pop("tax_basis", None)
    _, transactions, report = ci.convert(_document([order]))
    splits = _splits_by_suffix(transactions[0])

    assert report["rejected"] == []
    assert splits["REVENUE"] == pytest.approx(-100.0)


def test_an_inclusive_record_that_still_does_not_reconcile_is_rejected():
    """The basis explains a specific shape, and must not excuse every gap."""
    broken = _order(
        tax_basis="inclusive",
        amounts={
            "subtotal": "22.42", "shipping": "10.0", "tax": "1.24", "total": "999.0",
        },
    )
    _, transactions, report = ci.convert(_document([broken]))

    assert transactions == []
    assert len(report["rejected"]) == 1
    assert "reconcile" in report["rejected"][0]["reason"]


def test_per_line_tax_rounding_is_tolerated():
    """Sources round tax per line; an exact equality test would reject
    records that are correct as issued."""
    order = _order(amounts={
        "subtotal": "100.0", "shipping": "0.0", "tax": "20.0", "total": "120.004",
    })
    _, transactions, report = ci.convert(_document([order]))

    assert len(transactions) == 1
    assert report["rejected"] == []


def test_an_unrecognized_record_type_is_reported():
    _, transactions, report = ci.convert(
        _document([{"record_id": "X", "record_type": "refund_note"}])
    )

    assert transactions == []
    assert "unrecognized record_type" in report["rejected"][0]["reason"]


def test_a_record_without_an_id_is_rejected():
    order = _order()
    del order["record_id"]
    _, transactions, report = ci.convert(_document([order]))

    assert transactions == []
    assert "idempotent" in report["rejected"][0]["reason"]


def test_mixed_currencies_raise_a_warning():
    other = _order(record_id="SHOPIFY_RZL_ORDER_10319", currency="USD")
    _, _, report = ci.convert(_document([_order(), other]))

    assert report["currencies"] == ["GBP", "USD"]
    assert report["warnings"]
    assert "mixes currencies" in report["warnings"][0]


# -- document validation ----------------------------------------------------


def test_a_document_with_the_wrong_schema_is_refused(tmp_path):
    path = tmp_path / "raw.json"
    path.write_text(json.dumps({"schema": "something-else/v1"}))

    with pytest.raises(ValueError, match="expected schema"):
        ci.load_commerce_document(str(path))


def test_a_document_without_an_entity_code_is_refused(tmp_path):
    document = _document()
    document["entity"] = {}
    path = tmp_path / "d.json"
    path.write_text(json.dumps(document))

    with pytest.raises(ValueError, match="entity.code"):
        ci.load_commerce_document(str(path))


# -- CLI --------------------------------------------------------------------


def test_main_writes_a_feed_sync_fincosys_can_read(tmp_path, capsys):
    document = tmp_path / "orders.json"
    document.write_text(json.dumps(_document()))
    out = tmp_path / "feed" / "commerce_feed.json"

    assert ci.main([str(document), "--out", str(out)]) == 0

    accounts, transactions = sf.load_feed(str(out))
    assert len(accounts) == 4
    assert len(transactions) == 1
    assert "skipped aggregates" in capsys.readouterr().out


def test_main_reports_a_bad_document_without_a_traceback(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": "nope"}))

    assert ci.main([str(path)]) == 1
    assert "error:" in capsys.readouterr().err


# -- cross-repo contract test against real captured records -----------------


@pytest.mark.skipif(
    not os.path.exists(FIXTURE_PATH), reason="commerce fixture not present"
)
def test_real_shopify_records_produce_a_clean_sync_plan():
    document = ci.load_commerce_document(FIXTURE_PATH)
    accounts, transactions, report = ci.convert(document, source_path=FIXTURE_PATH)

    # Everything in this capture is bookable and reconciles.
    assert report["rejected"] == []
    assert report["booked"] == report["records_total"]
    assert report["booked"] > 0

    plan = sf.build_plan(accounts, transactions, source="commerce-fixture")

    assert plan["validation"]["is_clean"], plan["validation"]
    assert plan["validation"]["unbalanced_transactions"] == []
    assert plan["validation"]["unknown_account_refs"] == []
    assert plan["validation"]["duplicate_txids"] == []
    assert plan["validation"]["ok_transaction_count"] == len(transactions)


@pytest.mark.skipif(
    not os.path.exists(FIXTURE_PATH), reason="commerce fixture not present"
)
def test_real_records_book_revenue_matching_the_documents_own_totals():
    """The booked revenue+shipping+tax must equal the sum of order totals.

    This is the check that would catch a sign error or a dropped component
    silently shrinking recognized revenue.
    """
    document = ci.load_commerce_document(FIXTURE_PATH)
    _, transactions, _ = ci.convert(document)

    booked_receivable = sum(
        s["amount"]
        for t in transactions
        for s in t["splits"]
        if s["account_code"] == "COMM-RZL-AR"
    )
    document_totals = sum(
        float(r["amounts"]["total"])
        for r in document["records"]
        if r["record_type"] in ci.BOOKABLE_RECORD_TYPES
    )

    assert booked_receivable == pytest.approx(document_totals, abs=0.01)


# ---------------------------------------------------------------------------
# Ecosystem-sync input: accospace's hypergraph export carries commerce records
# in a `commerce` section. That is this repository's real integration surface
# with accospace -- the same document already carries organizations and
# cognitive atoms -- and until the section existed the ecosystem path carried
# no sales at all.
# ---------------------------------------------------------------------------


def _ecosystem_document(records_by_entity=None, counterparties=None):
    return {
        "schema": "fincosys-ecosystem-sync/v1",
        "source": "fincosys-atomspace-builder",
        "generated_at": "2026-09-22T00:00:00Z",
        "organizations": [],
        "atoms": [],
        "links": [],
        "commerce": {
            "record_schema": "fincosys-commerce-sync/v1",
            "records_by_entity": records_by_entity
            if records_by_entity is not None
            else {"RZL": [_ecosystem_record()]},
            "counterparties": counterparties
            if counterparties is not None
            else [
                {
                    "id": "COMM_PARTY_SHOPIFY_7554686779459",
                    "name": "Kat Buckley",
                    "external_id": "7554686779459",
                    "country": "GB",
                    "source": "shopify",
                }
            ],
        },
    }


def _ecosystem_record(**overrides):
    record = {
        "record_id": "SHOPIFY_RZL_ORDER_10318",
        "record_type": "sales_order",
        "external_id": "13317005050230",
        "document_number": "#10318",
        "issued_at": "2026-08-01T11:36:15Z",
        "currency": "GBP",
        "source": "shopify",
        "capture_status": "complete",
        "counterparty_ref": "COMM_PARTY_SHOPIFY_7554686779459",
        "amounts": {
            "subtotal": "329.5",
            "shipping": "17.05",
            "tax": "65.9",
            "total": "412.45",
        },
    }
    record.update(overrides)
    return record


# -- mixed currencies -------------------------------------------------------
#
# Every account commerce_import creates is single-currency, so a document
# stating more than one has no single right set of accounts. The default is
# to book only the primary currency and reject the rest, because the
# alternative -- booking a EUR total into a GBP receivable -- puts a wrong
# number in the ledger that no later reconciliation can distinguish from a
# real balance.


def _eur_order(**overrides):
    record = _order(
        record_id="SHOPIFY_RZL_ORDER_10319",
        document_number="#10319",
        currency="EUR",
        amounts={
            "subtotal": "100.0",
            "discounts": "0.0",
            "shipping": "10.0",
            "tax": "20.0",
            "total": "130.0",
        },
    )
    record.update(overrides)
    return record


def test_ecosystem_sync_expands_into_one_document_per_entity():
    documents = ci.documents_from_ecosystem_sync(
        _ecosystem_document(
            records_by_entity={
                "RZL": [_ecosystem_record()],
                "DRH": [
                    _ecosystem_record(
                        record_id="QBO_DRH_INVOICE_1",
                        record_type="sales_invoice",
                        counterparty_ref=None,
                        source="quickbooks",
                    )
                ],
            },
            counterparties=[],
        )
    )

    assert [d["entity"]["code"] for d in documents] == ["DRH", "RZL"]
    assert all(d["schema"] == ci.SCHEMA for d in documents)
    # The converter books per entity, so an entity's records must not be
    # split across two documents.
    assert [len(d["records"]) for d in documents] == [1, 1]


def test_ecosystem_sync_rehydrates_the_referenced_counterparty():
    """Counterparties are carried once and referenced, not inlined.

    A customer appears on many documents; the reference has to be resolved
    back onto each record or the booked transaction loses who it was with.
    """
    documents = ci.documents_from_ecosystem_sync(_ecosystem_document())

    record = documents[0]["records"][0]
    assert record["counterparty"]["name"] == "Kat Buckley"
    assert record["counterparty"]["external_id"] == "7554686779459"
    assert "counterparty_ref" not in record


def test_ecosystem_sync_books_through_the_same_converter():
    documents = ci.documents_from_ecosystem_sync(_ecosystem_document())
    accounts, transactions, report = ci.convert(documents[0])

    assert report["booked"] == 1
    assert report["rejected"] == []
    assert len(accounts) == 4
    total = sum(round(float(s["amount"]), 2) for s in transactions[0]["splits"])
    assert abs(total) <= sf.BALANCE_TOLERANCE


def test_one_partial_record_makes_the_whole_rebuilt_document_partial():
    """Capture status is per record there and per document here.

    Marking the document complete because most of its records were would
    state, of a set containing a known-partial capture, that it is the
    entity's full ledger.
    """
    documents = ci.documents_from_ecosystem_sync(
        _ecosystem_document(
            records_by_entity={
                "RZL": [
                    _ecosystem_record(),
                    _ecosystem_record(
                        record_id="SHOPIFY_RZL_ORDER_10319",
                        capture_status="partial",
                    ),
                ]
            }
        )
    )

    assert documents[0]["window"]["complete"] is False


def test_tax_basis_survives_into_the_booking():
    """A VAT-inclusive record read as exclusive is rejected, not booked.

    accospace used to drop tax_basis when loading a record into the
    hypergraph, so the three VAT-inclusive orders in the RegimA Zone store's
    history came back out looking exclusive and failed the identity check.
    """
    documents = ci.documents_from_ecosystem_sync(
        _ecosystem_document(
            records_by_entity={
                "RZL": [
                    _ecosystem_record(
                        record_id="SHOPIFY_RZL_ORDER_1004",
                        tax_basis="inclusive",
                        amounts={
                            "subtotal": "24.21",
                            "shipping": "0.0",
                            "tax": "4.03",
                            "total": "24.21",
                        },
                    )
                ]
            }
        )
    )
    _, transactions, report = ci.convert(documents[0])

    assert report["rejected"] == []
    assert report["booked"] == 1
    # Revenue is credited net of the tax the subtotal contains.
    revenue = [
        s for s in transactions[0]["splits"] if s["account_code"].endswith("REVENUE")
    ][0]
    assert round(float(revenue["amount"]), 2) == -20.18


def test_an_empty_entity_code_is_refused_rather_than_attributed():
    with pytest.raises(ValueError) as excinfo:
        ci.documents_from_ecosystem_sync(
            _ecosystem_document(records_by_entity={"": [_ecosystem_record()]})
        )
    assert "cannot be attributed" in str(excinfo.value)


def test_a_document_with_no_commerce_section_yields_nothing():
    """An older accospace export predates the section; that is not an error."""
    assert ci.documents_from_ecosystem_sync({"schema": "fincosys-ecosystem-sync/v1"}) == []


def test_load_documents_dispatches_on_schema(tmp_path):
    eco = tmp_path / "eco.json"
    eco.write_text(json.dumps(_ecosystem_document()))
    loaded = ci.load_documents(str(eco))
    assert len(loaded) == 1
    document, label = loaded[0]
    assert document["schema"] == ci.SCHEMA
    # The label still names where the records came from.
    assert label.endswith("#RZL")

    commerce = tmp_path / "commerce.json"
    commerce.write_text(json.dumps(_document()))
    loaded = ci.load_documents(str(commerce))
    assert len(loaded) == 1
    assert loaded[0][1] == str(commerce)


def test_a_malformed_commerce_document_still_names_its_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "something-else/v1"}))
    with pytest.raises(ValueError) as excinfo:
        ci.load_documents(str(bad))
    assert "bad.json" in str(excinfo.value)


# ---------------------------------------------------------------------------
# --repos-root discovery. The corpus is 40-odd repositories; a capture named
# by hand is a capture that can be left out without anyone noticing.
# ---------------------------------------------------------------------------


def _entity_repo(root, name, relative, document):
    path = root / name / os.path.dirname(relative)
    path.mkdir(parents=True, exist_ok=True)
    target = root / name / relative
    target.write_text(json.dumps(document))
    return target


def test_discovery_finds_documents_across_entity_repos(tmp_path):
    _entity_repo(tmp_path, "entity-rzl",
                 "accounting/shopify/raw-json/orders.json", _document())
    _entity_repo(tmp_path, "entity-rzl",
                 "accounting/shopify/reports/ledger.json", _document())
    _entity_repo(tmp_path, "entity-drh",
                 "accounting/qbo/reports/invoices.json", _document())

    found, skipped = ci.discover_documents(str(tmp_path))

    assert len(found) == 3
    assert skipped == []
    assert any("entity-drh" in p for p in found)
    assert any("reports" in p for p in found)


def test_discovery_skips_files_that_are_not_this_scripts_input(tmp_path):
    """The canonical paths also hold raw provider exports and manifests.

    Those are not commerce documents and not errors either -- they are
    simply not input, so they are skipped rather than reported as failures.
    """
    _entity_repo(tmp_path, "entity-rzl",
                 "accounting/shopify/raw-json/orders.json", _document())
    _entity_repo(tmp_path, "entity-rzl",
                 "accounting/shopify/reports/manifest.json",
                 {"schema": "fincosys-commerce-sync-manifest/v1"})
    (tmp_path / "entity-rzl" / "accounting" / "shopify" / "raw-json"
     / "notjson.json").write_text("{{{ not json")

    found, _ = ci.discover_documents(str(tmp_path))

    assert len(found) == 1
    assert found[0].endswith("orders.json")


def test_discovery_finds_an_ecosystem_sync_document_too(tmp_path):
    _entity_repo(tmp_path, "entity-rzl",
                 "accounting/shopify/reports/eco.json", _ecosystem_document())
    found, _ = ci.discover_documents(str(tmp_path))
    assert len(found) == 1


def test_discovery_on_a_missing_root_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        ci.discover_documents(str(tmp_path / "nope"))


def test_convert_paths_reports_each_entity_separately(tmp_path):
    eco = tmp_path / "eco.json"
    eco.write_text(json.dumps(_ecosystem_document(
        records_by_entity={
            "RZL": [_ecosystem_record()],
            "DRH": [_ecosystem_record(record_id="QBO_DRH_INVOICE_1",
                                      record_type="sales_invoice",
                                      counterparty_ref=None)],
        },
        counterparties=[],
    )))

    accounts, transactions, reports, failures, dups = ci.convert_paths([str(eco)])
    assert failures == []
    assert dups["conflicts"] == []

    assert len(reports) == 2
    assert {r["entity_code"] for r in reports} == {"RZL", "DRH"}
    # Four accounts per entity, and they must not be merged across entities.
    assert len(accounts) == 8
    assert len(transactions) == 2


# ---------------------------------------------------------------------------
# Supersession. The corpus keeps dated captures side by side, so scanning it
# finds the same record twice -- RZL's Shopify history holds both the
# 109-order window of 2026-09-06 and the 9,449-order capture that superseded
# it. Booking both double-counts the sale.
# ---------------------------------------------------------------------------


def _capture(tmp_path, name, *, complete, generated_at, subtotal="329.5"):
    """One capture of one record.

    The amounts are varied through `subtotal` and kept internally
    reconciling, because a copy that fails the identity check is rejected
    before it can ever become a competing transaction -- which is not the
    situation these tests are about.
    """
    net = float(subtotal)
    shipping, tax = 17.05, round(net * 0.2, 2)
    document = _document(complete=complete)
    document["generated_at"] = generated_at
    document["records"][0]["amounts"] = {
        "subtotal": "{:.2f}".format(net),
        "shipping": "{:.2f}".format(shipping),
        "tax": "{:.2f}".format(tax),
        "total": "{:.2f}".format(net + shipping + tax),
    }
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return str(path)


def test_the_more_complete_capture_wins(tmp_path):
    partial = _capture(tmp_path, "window.json", complete=False,
                       generated_at="2026-09-22T00:00:00Z")
    full = _capture(tmp_path, "history.json", complete=True,
                    generated_at="2026-09-06T00:00:00Z")

    _, transactions, _, _, duplicates = ci.convert_paths([partial, full])

    assert len(transactions) == 1
    # Complete beats partial even though the partial capture is newer: a
    # window covering the entity's history is the better authority.
    assert duplicates["superseded"][0]["kept"] == full
    assert duplicates["superseded"][0]["dropped"] == [partial]


def test_among_equal_captures_the_later_one_wins(tmp_path):
    older = _capture(tmp_path, "older.json", complete=True,
                     generated_at="2026-09-06T00:00:00Z")
    newer = _capture(tmp_path, "newer.json", complete=True,
                     generated_at="2026-09-22T00:00:00Z")

    _, transactions, _, _, duplicates = ci.convert_paths([older, newer])

    assert len(transactions) == 1
    assert duplicates["superseded"][0]["kept"] == newer


def test_two_captures_that_disagree_are_a_conflict_and_neither_books(tmp_path):
    """Picking one would answer a question about the evidence silently.

    This is the rule the statement corpus already follows: two extracts of
    one statement whose figures disagree are left flagged, not filed away as
    a duplicate.
    """
    a = _capture(tmp_path, "a.json", complete=True,
                 generated_at="2026-09-06T00:00:00Z", subtotal="329.5")
    b = _capture(tmp_path, "b.json", complete=True,
                 generated_at="2026-09-22T00:00:00Z", subtotal="800.00")

    _, transactions, _, _, duplicates = ci.convert_paths([a, b])

    assert transactions == []
    assert duplicates["superseded"] == []
    assert len(duplicates["conflicts"]) == 1
    assert duplicates["conflicts"][0]["sources"] == sorted([a, b])


def test_a_conflict_is_not_resolved_by_preferring_the_newer_capture(tmp_path):
    """A newer capture is a better *capture*, not a licence to overwrite.

    Supersession applies where the copies agree. Where they disagree the
    recency rule must not kick in, or a conflict silently becomes a
    supersession and the disagreement is never seen.
    """
    old_complete = _capture(tmp_path, "old.json", complete=True,
                            generated_at="2026-01-01T00:00:00Z",
                            subtotal="100.00")
    new_partial = _capture(tmp_path, "new.json", complete=False,
                           generated_at="2026-09-22T00:00:00Z",
                           subtotal="200.00")

    _, transactions, _, _, duplicates = ci.convert_paths(
        [old_complete, new_partial])

    assert transactions == []
    assert len(duplicates["conflicts"]) == 1


def test_distinct_records_are_not_collapsed(tmp_path):
    first = _document()
    second = _document(records=[_order(record_id="SHOPIFY_RZL_ORDER_10319")])
    paths = []
    for name, document in (("a.json", first), ("b.json", second)):
        path = tmp_path / name
        path.write_text(json.dumps(document))
        paths.append(str(path))

    _, transactions, _, _, duplicates = ci.convert_paths(paths)

    assert len(transactions) == 2
    assert duplicates["superseded"] == []
    assert duplicates["conflicts"] == []


def test_same_amount_different_decomposition_is_supersession_not_conflict(tmp_path):
    """Two captures of one invoice routinely differ in detail, not in figures.

    RZL's QuickBooks invoices are held both ways in the corpus: the
    2026-09-06 export states total, tax and balance only; the 2026-09-07 one
    adds subtotal, shipping and discounts. All 1,000 overlapping invoices
    agree on every stated figure. Treating the extra detail as a conflict
    would refuse to book a thousand real invoices over a disagreement that
    does not exist.
    """
    coarse = _document(complete=True)
    coarse["generated_at"] = "2026-09-06T00:00:00Z"
    coarse["records"][0]["amounts"] = {
        "subtotal": "394.55", "shipping": "0.00", "tax": "17.90",
        "total": "412.45",
    }
    fine = _document(complete=True)
    fine["generated_at"] = "2026-09-07T00:00:00Z"
    fine["records"][0]["amounts"] = {
        "subtotal": "329.50", "shipping": "65.05", "tax": "17.90",
        "total": "412.45",
    }

    paths = []
    for name, document in (("coarse.json", coarse), ("fine.json", fine)):
        path = tmp_path / name
        path.write_text(json.dumps(document))
        paths.append(str(path))

    _, transactions, _, _, duplicates = ci.convert_paths(paths)

    assert duplicates["conflicts"] == []
    assert len(duplicates["superseded"]) == 1
    assert len(transactions) == 1
    # Both charge 412.45; that is what had to agree.
    debit = sum(
        round(float(s["amount"]), 2)
        for s in transactions[0]["splits"]
        if float(s["amount"]) > 0
    )
    assert debit == 412.45


def test_the_better_decomposed_copy_wins_even_if_it_is_older(tmp_path):
    """Detail breaks the tie before recency.

    A sale booked across revenue, shipping and tax is a better record than
    the same sale booked to one account, whatever the capture dates.
    """
    detailed_old = _document(complete=True)
    detailed_old["generated_at"] = "2026-01-01T00:00:00Z"
    detailed_old["records"][0]["amounts"] = {
        "subtotal": "329.50", "shipping": "17.05", "tax": "65.90",
        "total": "412.45",
    }
    coarse_new = _document(complete=True)
    coarse_new["generated_at"] = "2026-09-22T00:00:00Z"
    coarse_new["records"][0]["amounts"] = {
        "subtotal": "412.45", "shipping": "0.00", "tax": "0.00",
        "total": "412.45",
    }

    paths = []
    for name, document in (("old.json", detailed_old), ("new.json", coarse_new)):
        path = tmp_path / name
        path.write_text(json.dumps(document))
        paths.append(str(path))

    _, transactions, _, _, duplicates = ci.convert_paths(paths)

    assert len(transactions) == 1
    assert duplicates["superseded"][0]["kept"].endswith("old.json")
    assert len(transactions[0]["splits"]) == 4


def test_an_estimate_is_skipped_as_a_quote_not_rejected_as_unrecognized():
    """An estimate is a quote, not a sale, and not a broken record either.

    Booking one credits revenue for a transaction that may never happen, and
    if it does happen the invoice raised against it books the same revenue
    again. Reporting it as an unrecognized record_type reads as a fault in
    the capture; entity-rzl holds 38 perfectly good estimates.
    """
    document = _document(records=[
        _order(record_id="QBO_RZL_ESTIMATE_1", record_type="sales_estimate")
    ])

    _, transactions, report = ci.convert(document)

    assert transactions == []
    assert report["rejected"] == []
    assert report["skipped_not_bookable"] == 1
    assert report["booked"] == 0


def test_a_genuinely_unknown_record_type_is_still_rejected():
    document = _document(records=[
        _order(record_id="X", record_type="something_new")
    ])

    _, _, report = ci.convert(document)

    assert len(report["rejected"]) == 1
    assert "unrecognized record_type" in report["rejected"][0]["reason"]


def test_a_foreign_currency_record_is_rejected_rather_than_misbooked():
    _, transactions, report = ci.convert(_document([_order(), _eur_order()]))

    assert [t["txid"] for t in transactions] == ["COMMERCE:SHOPIFY_RZL_ORDER_10318"]
    assert len(report["rejected"]) == 1
    rejection = report["rejected"][0]
    assert rejection["record_id"] == "SHOPIFY_RZL_ORDER_10319"
    assert rejection["currency"] == "EUR"
    assert rejection["primary_currency"] == "GBP"
    assert report["booked_currencies"] == ["GBP"]


def test_no_foreign_currency_account_is_created_when_the_record_is_rejected():
    """A rejected record must not leave an empty account behind in the plan."""
    accounts, _, _ = ci.convert(_document([_order(), _eur_order()]))

    assert all("EUR" not in a["code"] for a in accounts)
    assert {a["currency"] for a in accounts} == {"GBP"}


def test_per_currency_accounts_books_the_foreign_record_into_its_own_accounts():
    accounts, transactions, report = ci.convert(
        _document([_order(), _eur_order()]), per_currency_accounts=True
    )

    assert report["rejected"] == []
    assert report["booked"] == 2
    assert report["booked_currencies"] == ["EUR", "GBP"]

    by_code = {a["code"]: a for a in accounts}
    # The primary currency keeps the codes it always had, so a book already
    # imported from a single-currency document still matches.
    assert by_code["COMM-RZL-AR"]["currency"] == "GBP"
    assert by_code["COMM-RZL-EUR-AR"]["currency"] == "EUR"

    eur_txn = next(t for t in transactions if t["currency"] == "EUR")
    assert all(s["account_code"].startswith("COMM-RZL-EUR-")
               for s in eur_txn["splits"])
    gbp_txn = next(t for t in transactions if t["currency"] == "GBP")
    assert all("EUR" not in s["account_code"] for s in gbp_txn["splits"])


def test_a_single_currency_document_is_unaffected_by_the_flag():
    """--per-currency-accounts must not rename a single-currency document's
    accounts, or re-importing one would duplicate every account in the book."""
    plain = ci.convert(_document())
    scoped = ci.convert(_document(), per_currency_accounts=True)

    assert [a["code"] for a in plain[0]] == [a["code"] for a in scoped[0]]
    assert [t["splits"] for t in plain[1]] == [t["splits"] for t in scoped[1]]


# -- real QuickBooks records ------------------------------------------------


@pytest.mark.skipif(
    not os.path.exists(QBO_FIXTURE_PATH), reason="quickbooks fixture not present"
)
def test_real_quickbooks_records_produce_a_clean_sync_plan():
    """The cross-repo contract test for the QuickBooks side.

    The Shopify fixture above is single-currency; this one is the real
    RegimA @ Dr H Ltd ledger, which states GBP and EUR in one document, so
    it is what exercises the mixed-currency path end to end.
    """
    document = ci.load_commerce_document(QBO_FIXTURE_PATH)
    accounts, transactions, report = ci.convert(
        document, source_path=QBO_FIXTURE_PATH, per_currency_accounts=True
    )

    assert report["rejected"] == []
    assert report["booked"] == report["records_total"]
    assert report["booked_currencies"] == ["EUR", "GBP"]

    plan = sf.build_plan(accounts, transactions, source="commerce-qbo-fixture")

    assert plan["validation"]["is_clean"], plan["validation"]
    assert plan["validation"]["unbalanced_transactions"] == []
    assert plan["validation"]["unknown_account_refs"] == []
    assert plan["validation"]["duplicate_txids"] == []
    assert plan["validation"]["ok_transaction_count"] == len(transactions)


@pytest.mark.skipif(
    not os.path.exists(QBO_FIXTURE_PATH), reason="quickbooks fixture not present"
)
def test_real_quickbooks_records_keep_each_currency_in_its_own_accounts():
    """Each currency's receivable must equal that currency's own totals.

    A cross-currency leak would still balance per transaction, so only this
    per-currency comparison catches it.
    """
    document = ci.load_commerce_document(QBO_FIXTURE_PATH)
    _, transactions, _ = ci.convert(document, per_currency_accounts=True)

    for currency, code in (("GBP", "COMM-RDH-AR"), ("EUR", "COMM-RDH-EUR-AR")):
        booked = sum(
            s["amount"]
            for t in transactions
            for s in t["splits"]
            if s["account_code"] == code
        )
        stated = sum(
            float(r["amounts"]["total"])
            for r in document["records"]
            if r["currency"] == currency
            and r["record_type"] in ci.BOOKABLE_RECORD_TYPES
        )
        assert booked == pytest.approx(stated, abs=0.01), currency
