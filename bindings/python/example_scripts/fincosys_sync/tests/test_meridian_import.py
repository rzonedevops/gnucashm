#!/usr/bin/env python3

# test_meridian_import.py -- covers meridian_import.py, which books a
# QuickBooks general ledger captured through Meridian (schema
# fincosys.qbo_meridian_capture.v1) as a sync feed sync_fincosys.py plans.
# Captures are built in a temporary directory with the same layout the
# entity repositories hold. Pure Python; no built GnuCash needed.

import csv
import gzip
import hashlib
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import meridian_import as mi  # noqa: E402
import sync_fincosys as sf  # noqa: E402

FIELDS = ["section_id", "section", "tx_date", "txn_type", "txn_type_id",
          "doc_num", "name", "name_id", "memo", "account_num", "account_name",
          "account_name_id", "split_acc", "split_acc_id", "debt_amt",
          "credit_amt", "amount", "currency", "exch_rate", "is_cleared",
          "create_date", "last_mod_date", "chunk"]


def _row(acct_id, name, ttype, tid, debit="", credit="", date="2025-01-02",
         num="", **kw):
    r = dict.fromkeys(FIELDS, "")
    r.update(section_id=acct_id, section=name, tx_date=date, txn_type=ttype,
             txn_type_id=tid, account_name=name, account_name_id=acct_id,
             account_num=num, debt_amt=debit, credit_amt=credit)
    r.update(kw)
    return r


BANK = ("10", "Bank:Current")
SALES = ("20", "Sales")
AR = ("30", "Accounts Receivable (A/R)")

ROWS = [
    _row(*BANK, "Deposit", "1", debit="100.00"),
    _row(*SALES, "Deposit", "1", credit="100.00"),
    # Journal Entry 1 is a different transaction from Deposit 1.
    _row(*AR, "Journal Entry", "1", debit="40.00"),
    _row(*SALES, "Journal Entry", "1", credit="40.00"),
    _row("", "", "Payment", "7", debit=".00", section="Not Specified"),
    _row(*AR, "Payment", "7", credit=""),
]


def _write_capture(root, repo, captured, rows, entity="TST", company="c-1",
                   tamper=False):
    d = os.path.join(root, repo, "accounting", "qbo", "meridian", captured)
    os.makedirs(d)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    raw = buf.getvalue().encode("utf-8")
    with gzip.open(os.path.join(d, "general_ledger.csv.gz"), "wb") as fh:
        fh.write(raw + (b"x" if tamper else b""))
    bank_rows = [r for r in rows if r["account_name_id"] == BANK[0]]
    bbuf = io.StringIO()
    bw = csv.DictWriter(bbuf, fieldnames=FIELDS, lineterminator="\n")
    bw.writeheader()
    bw.writerows(bank_rows)
    with gzip.open(os.path.join(d, "bank_ledger.csv.gz"), "wb") as fh:
        fh.write(bbuf.getvalue().encode("utf-8"))
    manifest = {
        "schema": mi.MANIFEST_SCHEMA, "captured": captured,
        "company_id": company, "company": "Test Co", "entity": entity,
        "realm_id": "999", "currency": "ZAR",
        "general_ledger": {"file": "general_ledger.csv.gz",
                           "postings": len(rows),
                           "csv_sha256": hashlib.sha256(raw).hexdigest()},
        "bank_ledger": {"file": "bank_ledger.csv.gz",
                        "per_ledger": {"10|Current": len(bank_rows)}},
    }
    with open(os.path.join(d, "MANIFEST.json"), "w") as fh:
        json.dump(manifest, fh)
    return d


def test_groups_postings_by_type_and_id_and_plans_clean(tmp_path):
    _write_capture(str(tmp_path), "entity-tst", "2026-11-01", ROWS)
    feed, report = mi.convert_root(str(tmp_path))
    txids = sorted(t["txid"] for t in feed["transactions"])
    assert txids == [
        "QBO-GL:TST:999:Deposit:1",
        "QBO-GL:TST:999:Journal Entry:1",
        "QBO-GL:TST:999:Payment:7",
    ]
    plan = sf.build_plan(feed["accounts"], feed["transactions"])
    assert plan["validation"]["is_clean"]
    book = report["books"][0]
    assert book["capture_net"] == 0
    assert book["bank_ledger_check"]["status"] == "agrees"


def test_account_types_and_not_specified(tmp_path):
    _write_capture(str(tmp_path), "entity-tst", "2026-11-01", ROWS)
    feed, _ = mi.convert_root(str(tmp_path))
    by_id = {a["metadata"]["qbo_account_id"]: a for a in feed["accounts"]}
    assert by_id["10"]["account_type"] == "BANK"
    assert by_id["10"]["metadata"]["type_basis"] == "bank ledger"
    assert by_id["30"]["metadata"]["type_basis"] == "system name"
    assert by_id[mi.NOT_SPECIFIED_ID]["name"] == "Not Specified"
    # Colons are QuickBooks' path separator, not part of a GnuCash name.
    assert by_id["10"]["name"] == "Bank / Current"


def test_account_list_beats_inference(tmp_path):
    _write_capture(str(tmp_path), "entity-tst", "2026-11-01", ROWS)
    feed, _ = mi.convert_root(str(tmp_path),
                              extra_types={"TST": {"20": "Income"}})
    sales = [a for a in feed["accounts"]
             if a["metadata"]["qbo_account_id"] == "20"][0]
    assert (sales["account_type"], sales["metadata"]["type_basis"]) == (
        "INCOME", "account list")


def test_unbalanced_transaction_is_reported_not_booked(tmp_path):
    rows = ROWS + [_row(*BANK, "Deposit", "2", debit="5.00")]
    _write_capture(str(tmp_path), "entity-tst", "2026-11-01", rows)
    feed, report = mi.convert_root(str(tmp_path))
    assert "QBO-GL:TST:999:Deposit:2" not in {
        t["txid"] for t in feed["transactions"]}
    assert report["books"][0]["unbalanced"][0]["imbalance"] == 5.0


def test_tampered_capture_is_refused(tmp_path):
    _write_capture(str(tmp_path), "entity-tst", "2026-11-01", ROWS,
                   tamper=True)
    feed, report = mi.convert_root(str(tmp_path))
    assert feed["transactions"] == []
    assert "sha256" in report["refused"][0]["reason"]


def test_newest_capture_wins_and_identical_copies_book_once(tmp_path):
    root = str(tmp_path)
    _write_capture(root, "entity-a", "2026-11-01", ROWS[:2])
    _write_capture(root, "entity-a", "2026-11-01b", ROWS)
    _write_capture(root, "entity-mirror", "2026-11-01b", ROWS)
    feed, report = mi.convert_root(root)
    assert len(feed["transactions"]) == 3
    sel = report["selection"]
    assert len(sel["superseded"]) == 1
    assert len(sel["identical_copies"]) == 1
    assert sel["conflicts"] == []


def test_differing_copies_of_one_capture_are_a_conflict(tmp_path):
    root = str(tmp_path)
    _write_capture(root, "entity-a", "2026-11-01", ROWS)
    _write_capture(root, "entity-b", "2026-11-01", ROWS[:2])
    feed, report = mi.convert_root(root)
    assert feed["transactions"] == []
    assert len(report["selection"]["conflicts"]) == 1


def test_alias_books_under_canonical_code(tmp_path):
    _write_capture(str(tmp_path), "entity-tst", "2026-11-01", ROWS,
                   entity="OLD")
    feed, report = mi.convert_root(str(tmp_path), aliases={"OLD": "TST"})
    assert {a["entity_code"] for a in feed["accounts"]} == {"TST"}
    assert report["books"][0]["declared_entity"] == "OLD"


@pytest.mark.parametrize("num,kind", [
    ("1000/000", "INCOME"), ("2000/000", "EXPENSE"), ("2750/000", "INCOME"),
    ("4400/000", "EXPENSE"), ("5200/000", "EQUITY"), ("5466/000", "LIABILITY"),
    ("8400/000", "ASSET"), ("9500/000", "LIABILITY"), ("Q1", None),
])
def test_pastel_class(num, kind):
    assert mi.pastel_class(num) == kind
