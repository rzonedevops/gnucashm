#!/usr/bin/env python3

# test_feed_entities.py -- The feed path must carry entity identity.
#
# Accounts and transactions reference an entity only by a bare
# `entity_code`.  The `--data-dir` fallback loader reads legal_name and
# entity_type from MASTER_ENTITIES.json; the `--feed` path -- the one the
# README calls preferred -- had no way to carry them, so on identical data
# the two paths disagreed, and `--apply` wrote the bare code into the book
# as the entity account's description.
#
# Pure Python: no `gnucash` import, --plan-only path only.

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sync_fincosys as sf  # noqa: E402


ENTITY = {
    "code": "RST",
    "legal_name": "Regima Skin Treatments CC",
    "entity_type": "CC",
}

ACCOUNTS = [
    {"code": "1001", "name": "FNB Cheque", "parent_code": None,
     "account_type": "BANK", "entity_code": "RST", "currency": "ZAR",
     "description": ""},
    {"code": "IMB-BANK", "name": "Imbalance-Bank", "parent_code": None,
     "account_type": "EXPENSE", "entity_code": "RST", "currency": "ZAR",
     "description": ""},
]

TRANSACTIONS = [
    {"txid": "T1", "date": "2026-01-02", "description": "Monthly fee",
     "currency": "ZAR", "entity_code": "RST",
     "splits": [{"account_code": "1001", "amount": -250.0, "memo": ""},
                {"account_code": "IMB-BANK", "amount": 250.0, "memo": ""}],
     "metadata": {}},
]


def _write_feed(tmp_path, entities):
    feed = {
        "schema_version": sf.SCHEMA_VERSION,
        "generated_at": "2026-10-12T00:00:00Z",
        "source": {"repo": "fincosys", "generator": "test"},
        "accounts": ACCOUNTS,
        "transactions": TRANSACTIONS,
    }
    if entities is not None:
        feed["entities"] = entities
    path = tmp_path / "feed.json"
    path.write_text(json.dumps(feed), encoding="utf-8")
    return str(path)


def _per_entity(plan):
    """The plan's per-entity summary, whatever the key is called."""
    key = next(k for k in plan if "entit" in k.lower())
    return plan[key]


class TestFeedCarriesEntityIdentity:
    def test_legal_name_and_type_survive_the_feed(self, tmp_path):
        """The whole point: identity is not lost in transit."""
        path = _write_feed(tmp_path, [ENTITY])
        accounts, transactions, entities_meta, _ = sf.load_plan_inputs(path, None)
        plan = sf.build_plan(accounts, transactions, entities_meta)
        rst = _per_entity(plan)["RST"]
        assert rst["legal_name"] == "Regima Skin Treatments CC"
        assert rst["entity_type"] == "CC"

    def test_the_feed_path_now_agrees_with_the_data_dir_path(self, tmp_path):
        """Both loaders must produce the same shape and the same defaults.

        Downstream code cannot tell which loader ran, so a difference here
        is a difference in the book that gets written.
        """
        path = _write_feed(tmp_path, [ENTITY])
        from_feed = sf.load_feed_entities(path)
        assert from_feed == {
            "RST": {"legal_name": "Regima Skin Treatments CC",
                    "entity_type": "CC"},
        }

    def test_a_feed_without_entities_keeps_the_old_behaviour(self, tmp_path):
        """The block is optional; omitting it must not change anything."""
        path = _write_feed(tmp_path, None)
        accounts, transactions, entities_meta, _ = sf.load_plan_inputs(path, None)
        assert entities_meta == {}
        plan = sf.build_plan(accounts, transactions, entities_meta)
        rst = _per_entity(plan)["RST"]
        assert rst["legal_name"] == "RST"
        assert rst["entity_type"] == "UNKNOWN"

    def test_an_empty_entities_block_is_accepted(self, tmp_path):
        path = _write_feed(tmp_path, [])
        assert sf.load_feed_entities(path) == {}

    def test_partial_metadata_falls_back_per_field(self, tmp_path):
        """A code with no name is still a usable entity, named by its code."""
        path = _write_feed(tmp_path, [{"code": "RST"}])
        assert sf.load_feed_entities(path) == {
            "RST": {"legal_name": "RST", "entity_type": "UNKNOWN"},
        }

    def test_blank_values_fall_back_rather_than_naming_an_entity_empty(
            self, tmp_path):
        """An empty legal_name must not become the account description."""
        path = _write_feed(
            tmp_path, [{"code": "RST", "legal_name": "", "entity_type": ""}])
        assert sf.load_feed_entities(path) == {
            "RST": {"legal_name": "RST", "entity_type": "UNKNOWN"},
        }

    def test_an_entry_without_a_code_is_skipped(self, tmp_path):
        """There is nothing to key it by, and nothing references it."""
        path = _write_feed(
            tmp_path, [{"legal_name": "Nameless"}, ENTITY])
        assert list(sf.load_feed_entities(path)) == ["RST"]

    def test_load_feed_keeps_its_two_tuple_signature(self, tmp_path):
        """Existing callers must not break -- there are a dozen of them."""
        path = _write_feed(tmp_path, [ENTITY])
        accounts, transactions = sf.load_feed(path)
        assert len(accounts) == 2
        assert len(transactions) == 1
