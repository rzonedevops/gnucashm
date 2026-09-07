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

    accounts, transactions, reports = ci.convert_paths(paths)

    assert len(accounts) == 4
    assert len(transactions) == 2
    assert len(reports) == 2


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
