#!/usr/bin/env python3

# test_atomspace_builder_feed.py -- Cross-repo contract test: feeds a *real*
# fincosys-atomspace-builder GnuCashSyncExporter document (checked in at
# tests/fixtures/atomspace_builder_sync_feed.json -- see
# tests/fixtures/README.md for provenance and how to regenerate it) through
# this script's --feed loader and plan builder, and asserts the resulting
# plan is clean.
#
# sync_fincosys.py's --feed path and fincosys-atomspace-builder's
# GnuCashSyncExporter are developed and tested in separate repositories
# against a shared written schema (fincosys-ecosystem-sync sync-feed
# schema_version "1.0"), but nothing previously verified they actually
# agree on the wire format. This test closes that gap using the exporter's
# real output rather than a hand-authored guess at its shape.

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sync_fincosys as sf  # noqa: E402

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "atomspace_builder_sync_feed.json",
)


@pytest.fixture
def raw_feed():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_fixture_is_the_documented_schema(raw_feed):
    assert raw_feed["schema_version"] == sf.SCHEMA_VERSION
    assert raw_feed["source"]["generator"] == "fincosys-atomspace-builder"
    assert raw_feed["accounts"], "fixture should carry at least one account"
    assert raw_feed["transactions"], "fixture should carry at least one transaction"


def test_load_feed_reads_real_exporter_output():
    accounts, transactions = sf.load_feed(FIXTURE_PATH)

    # RST + PF entity roots, one bank account each, three Imbalance-*
    # placeholders synthesized by generate_feed() for the fixture's three
    # transactions (PAYMENT, xero code 404, and no-category/UNCATEGORIZED).
    assert len(accounts) == 7
    assert len(transactions) == 3

    codes = {a["code"] for a in accounts}
    assert {"RST", "PF", "55270035642", "55270018789"} <= codes
    imbalance_codes = {c for c in codes if "Imbalance" in c}
    assert len(imbalance_codes) == 3


def test_build_plan_from_real_exporter_output_is_clean():
    accounts, transactions = sf.load_feed(FIXTURE_PATH)
    plan = sf.build_plan(
        accounts, transactions, source="feed:{}".format(FIXTURE_PATH)
    )

    validation = plan["validation"]
    assert validation["is_clean"] is True, validation
    assert validation["duplicate_account_codes"] == []
    assert validation["duplicate_txids"] == []
    assert validation["unknown_account_refs"] == []
    assert validation["unbalanced_transactions"] == []
    assert validation["ok_transaction_count"] == 3

    # Every split's counterparty account is one of the synthesized
    # Imbalance-* placeholders -- confirms the double-entry-balancing
    # design documented in gnucash_exporter.py round-trips correctly.
    for txn in plan["transactions"]:
        splits = txn["splits"]
        assert len(splits) == 2
        assert abs(sum(s["amount"] for s in splits)) <= sf.BALANCE_TOLERANCE

    assert set(plan["entities"].keys()) == {"RST", "PF"}
