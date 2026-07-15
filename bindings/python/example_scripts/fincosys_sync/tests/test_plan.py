#!/usr/bin/env python3

# test_plan.py -- Pure-Python tests for the fincosys_sync plan builder and
# validator. Deliberately does NOT import `gnucash` -- this exercises only
# the --plan-only code path, which must run without a compiled GnuCash.

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sync_fincosys as sf  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixture feed
# ---------------------------------------------------------------------------
#
# 4 accounts (2 real bank accounts + 2 Imbalance placeholders), 4
# transactions:
#   TX-001  balanced, known accounts             -> should pass cleanly
#   TX-002  balanced, known accounts              -> should pass cleanly
#   TX-002  duplicate txid of the above            -> should be flagged
#   TX-003  references an unknown account code    -> should be flagged
#   TX-004  splits don't sum to zero               -> should be flagged
#
# (that's 5 transaction entries under 4 distinct txids, per the docstring
# above: "3-4 transactions including one duplicate txid and one
# referencing an unknown account")

def make_fixture_feed():
    accounts = [
        {
            "code": "ACC-RST-001",
            "name": "RST Main Account",
            "parent_code": None,
            "account_type": "BANK",
            "entity_code": "RST",
            "currency": "ZAR",
            "description": "RST FNB current account",
        },
        {
            "code": "ACC-SLG-001",
            "name": "SLG Main Account",
            "parent_code": None,
            "account_type": "BANK",
            "entity_code": "SLG",
            "currency": "ZAR",
            "description": "SLG FNB current account",
        },
        {
            "code": "IMBALANCE-RST-PAYMENT",
            "name": "Imbalance-PAYMENT",
            "parent_code": None,
            "account_type": "BANK",
            "entity_code": "RST",
            "currency": "ZAR",
            "description": "Auto-generated placeholder",
        },
        {
            "code": "IMBALANCE-SLG-INCOME",
            "name": "Imbalance-INCOME",
            "parent_code": None,
            "account_type": "BANK",
            "entity_code": "SLG",
            "currency": "ZAR",
            "description": "Auto-generated placeholder",
        },
    ]

    transactions = [
        {
            "txid": "TX-001",
            "date": "2025-01-05",
            "description": "Balanced RST payment",
            "currency": "ZAR",
            "entity_code": "RST",
            "splits": [
                {"account_code": "ACC-RST-001", "amount": -500.0, "memo": ""},
                {"account_code": "IMBALANCE-RST-PAYMENT", "amount": 500.0,
                 "memo": "fincosys auto-balance"},
            ],
            "metadata": {"is_intercompany": False, "category": "PAYMENT",
                         "xero_account_code": "400"},
        },
        {
            "txid": "TX-002",
            "date": "2025-01-06",
            "description": "Balanced SLG income",
            "currency": "ZAR",
            "entity_code": "SLG",
            "splits": [
                {"account_code": "ACC-SLG-001", "amount": 1200.0, "memo": ""},
                {"account_code": "IMBALANCE-SLG-INCOME", "amount": -1200.0,
                 "memo": "fincosys auto-balance"},
            ],
            "metadata": {"is_intercompany": False, "category": "INCOME",
                         "xero_account_code": "200"},
        },
        {
            # duplicate of TX-002's txid
            "txid": "TX-002",
            "date": "2025-01-07",
            "description": "Duplicate txid of TX-002",
            "currency": "ZAR",
            "entity_code": "SLG",
            "splits": [
                {"account_code": "ACC-SLG-001", "amount": 50.0, "memo": ""},
                {"account_code": "IMBALANCE-SLG-INCOME", "amount": -50.0,
                 "memo": "fincosys auto-balance"},
            ],
            "metadata": {"is_intercompany": False, "category": "INCOME",
                         "xero_account_code": "200"},
        },
        {
            "txid": "TX-003",
            "date": "2025-01-08",
            "description": "References an unknown account",
            "currency": "ZAR",
            "entity_code": "RST",
            "splits": [
                {"account_code": "ACC-RST-001", "amount": -75.0, "memo": ""},
                {"account_code": "ACC-DOES-NOT-EXIST", "amount": 75.0,
                 "memo": ""},
            ],
            "metadata": {"is_intercompany": None, "category": "FEE",
                         "xero_account_code": None},
        },
        {
            "txid": "TX-004",
            "date": "2025-01-09",
            "description": "Splits do not sum to zero",
            "currency": "ZAR",
            "entity_code": "RST",
            "splits": [
                {"account_code": "ACC-RST-001", "amount": -100.0, "memo": ""},
                {"account_code": "IMBALANCE-RST-PAYMENT", "amount": 99.0,
                 "memo": ""},
            ],
            "metadata": {"is_intercompany": False, "category": "PAYMENT",
                         "xero_account_code": "400"},
        },
    ]

    return {
        "schema_version": sf.SCHEMA_VERSION,
        "accounts": accounts,
        "transactions": transactions,
    }


@pytest.fixture
def fixture_feed_path(tmp_path):
    feed = make_fixture_feed()
    path = tmp_path / "fixture_feed.json"
    path.write_text(json.dumps(feed, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_feed_roundtrip(fixture_feed_path):
    accounts, transactions = sf.load_feed(fixture_feed_path)
    assert len(accounts) == 4
    assert len(transactions) == 5


def test_validator_catches_duplicate_txid(fixture_feed_path):
    accounts, transactions = sf.load_feed(fixture_feed_path)
    plan = sf.build_plan(accounts, transactions, source="test")
    assert "TX-002" in plan["validation"]["duplicate_txids"]


def test_validator_catches_unknown_account(fixture_feed_path):
    accounts, transactions = sf.load_feed(fixture_feed_path)
    plan = sf.build_plan(accounts, transactions, source="test")
    unknown_refs = plan["validation"]["unknown_account_refs"]
    assert any(
        r["txid"] == "TX-003" and r["account_code"] == "ACC-DOES-NOT-EXIST"
        for r in unknown_refs
    )


def test_validator_catches_unbalanced_transaction(fixture_feed_path):
    accounts, transactions = sf.load_feed(fixture_feed_path)
    plan = sf.build_plan(accounts, transactions, source="test")
    unbalanced = {
        item["txid"] for item in plan["validation"]["unbalanced_transactions"]
    }
    assert "TX-004" in unbalanced


def test_balanced_known_transactions_pass(fixture_feed_path):
    accounts, transactions = sf.load_feed(fixture_feed_path)
    plan = sf.build_plan(accounts, transactions, source="test")
    v = plan["validation"]
    # TX-001 and both TX-002 entries (the original and its txid-duplicate)
    # are individually balanced and reference only known accounts, so all
    # three count toward ok_transaction_count -- duplicate-txid detection
    # is a separate check (see test_validator_catches_duplicate_txid) and
    # does not by itself make a transaction's own splits invalid.
    # TX-003 (unknown account) and TX-004 (unbalanced) do not count.
    assert v["ok_transaction_count"] == 3
    assert v["total_transactions"] == 5
    assert v["is_clean"] is False  # because of the flagged issues above


def test_plan_is_clean_with_no_issues():
    accounts = [
        {"code": "A1", "name": "Acc 1", "parent_code": None,
         "account_type": "BANK", "entity_code": "RST", "currency": "ZAR",
         "description": ""},
        {"code": "A2", "name": "Acc 2", "parent_code": None,
         "account_type": "BANK", "entity_code": "RST", "currency": "ZAR",
         "description": ""},
    ]
    transactions = [
        {
            "txid": "OK-1",
            "date": "2025-01-01",
            "description": "clean",
            "currency": "ZAR",
            "entity_code": "RST",
            "splits": [
                {"account_code": "A1", "amount": 10.0, "memo": ""},
                {"account_code": "A2", "amount": -10.0, "memo": ""},
            ],
            "metadata": {"is_intercompany": False, "category": "TRANSFER",
                         "xero_account_code": "1"},
        },
    ]
    plan = sf.build_plan(accounts, transactions, source="test")
    v = plan["validation"]
    assert v["is_clean"] is True
    assert v["ok_transaction_count"] == 1
    assert v["duplicate_txids"] == []
    assert v["unknown_account_refs"] == []
    assert v["unbalanced_transactions"] == []


def test_per_entity_summary(fixture_feed_path):
    accounts, transactions = sf.load_feed(fixture_feed_path)
    plan = sf.build_plan(accounts, transactions, source="test")
    entities = plan["entities"]
    assert entities["RST"]["account_count"] == 2  # ACC-RST-001, IMBALANCE-RST-PAYMENT
    assert entities["SLG"]["account_count"] == 2
    assert entities["RST"]["transaction_count"] == 3  # TX-001, TX-003, TX-004
    assert entities["SLG"]["transaction_count"] == 2  # both TX-002 entries


def test_run_plan_only_writes_out_file(fixture_feed_path, tmp_path):
    out_path = tmp_path / "plan_out.json"

    class Args:
        feed = fixture_feed_path
        data_dir = None
        out = str(out_path)

    plan = sf.run_plan_only(Args())
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["validation"]["total_transactions"] == 5
    assert plan["validation"]["total_transactions"] == 5


def test_map_account_type_code_defaults_to_bank():
    assert sf.map_account_type_code("CUR") == "BANK"
    assert sf.map_account_type_code(None) == "BANK"
    assert sf.map_account_type_code("CC") == "LIABILITY"
    assert sf.map_account_type_code("INV") == "ASSET"
    assert sf.map_account_type_code("TRU") == "ASSET"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
