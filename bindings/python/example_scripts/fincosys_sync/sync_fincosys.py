#!/usr/bin/env python3

# sync_fincosys.py -- Sync fincosys financial-forensics data into a GnuCash
# book (or a dry-run "plan" that can be inspected/reviewed before applying).
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of
# the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, contact:
# Free Software Foundation           Voice:  +1-617-542-5942
# 51 Franklin Street, Fifth Floor    Fax:    +1-617-542-2652
# Boston, MA  02110-1301,  USA       gnu@gnu.org

##  @file
#   @brief Sync fincosys financial-forensics data (bank transactions across
#          22+ RegimA/Faucitt-matter group entities) into a GnuCash book
#   @ingroup python_bindings_examples
#
# Two modes:
#
#   --plan-only (default)
#       Pure Python. Loads a fincosys-atomspace-builder "sync feed" JSON
#       (--feed), or builds an equivalent structure directly from a
#       fincosys/data checkout (--data-dir) when no feed file is given.
#       Builds an in-memory plan (per-entity account tree + balanced
#       transaction list), validates it, and writes it to --out as JSON.
#       This mode never imports the compiled `gnucash` module, so it can
#       run anywhere Python 3 runs -- including CI, and this repo's own
#       build tree before GnuCash itself has been compiled.
#
#   --apply --book <path>
#       Imports `gnucash` and replays a previously-built plan (or builds
#       one on the fly from --feed/--data-dir) into a real GnuCash book,
#       creating the Account hierarchy and Transaction/Split objects.
#       Safe to re-run: transactions are keyed by fincosys txid and are
#       skipped if already present (see "Idempotency" in the README).
#
# See fincosys_sync/README.md for the full data-flow diagram and design
# rationale (especially the "Imbalance-<category>" placeholder accounts,
# which exist because fincosys's bank-statement lines are single-sided --
# there is no natural double-entry counterpart in the source data).
#
# Usage:
#   python3 sync_fincosys.py --data-dir /path/to/fincosys/data \
#       --plan-only --out sync_plan.json
#
#   python3 sync_fincosys.py --feed gnucash_sync_feed.json \
#       --apply --book /path/to/book.gnucash

import argparse
import datetime
import json
import os
import sys
from collections import OrderedDict, defaultdict

SCHEMA_VERSION = "1.0"

# fincosys transaction "type" -> GnuCash's Imbalance-<currency> accounts
# always use ACCT_TYPE_BANK (see libgnucash/engine/Scrub.cpp,
# xaccScrubUtilityGetOrMakeAccount(..., ACCT_TYPE_BANK, ...)). We follow
# the same convention for our per-entity, per-category placeholders.
IMBALANCE_ACCOUNT_TYPE = "BANK"

# Map fincosys/MASTER_ACCOUNTS.json `account_type_code` values onto the
# six-way account_type enum used by the sync feed schema
# (ASSET|LIABILITY|BANK|EXPENSE|INCOME|EQUITY). Every account in
# MASTER_ACCOUNTS.json is a real-world bank/FNB account extracted from a
# bank statement, so BANK is the correct default; a couple of codes are
# better represented by their natural balance-sheet category.
ACCOUNT_TYPE_CODE_MAP = {
    "CC": "LIABILITY",   # credit card facility
    "INV": "ASSET",      # investment account
    "TRU": "ASSET",      # trust account
}
DEFAULT_ACCOUNT_TYPE = "BANK"

BALANCE_TOLERANCE = 1e-6


def map_account_type_code(code):
    return ACCOUNT_TYPE_CODE_MAP.get((code or "").upper(), DEFAULT_ACCOUNT_TYPE)


def imbalance_account_code(entity_code, category):
    category = category or "UNCATEGORIZED"
    return "IMBALANCE-{}-{}".format(entity_code, category)


def imbalance_account_name(category):
    return "Imbalance-{}".format(category or "UNCATEGORIZED")


# ---------------------------------------------------------------------------
# Loading: sync feed JSON (fincosys-atomspace-builder GnuCashSyncExporter)
# ---------------------------------------------------------------------------

def load_feed(feed_path):
    """Load a fincosys-atomspace-builder sync feed JSON file.

    Returns (accounts, transactions) using the shapes documented in
    fincosys_sync/README.md (the same shapes the fallback loader below
    produces), so downstream code (build_plan/validate_plan) doesn't need
    to know which loader produced the data.
    """
    with open(feed_path, "r", encoding="utf-8") as fh:
        feed = json.load(fh)

    schema_version = feed.get("schema_version")
    if schema_version and schema_version != SCHEMA_VERSION:
        sys.stderr.write(
            "warning: feed schema_version {} != expected {}; "
            "proceeding anyway\n".format(schema_version, SCHEMA_VERSION)
        )

    accounts = list(feed.get("accounts", []))
    transactions = list(feed.get("transactions", []))
    return accounts, transactions


# ---------------------------------------------------------------------------
# Loading: fallback, built directly from fincosys/data/*.json
# ---------------------------------------------------------------------------

def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_fallback(data_dir):
    """Build an equivalent (accounts, transactions) structure directly from
    a fincosys/data checkout, for use when no sync feed JSON is available
    yet (fincosys-atomspace-builder's GnuCashSyncExporter may not exist in
    this environment).

    Reads:
      - MASTER_ENTITIES.json  (entities[]: code, legal_name, entity_type)
      - MASTER_ACCOUNTS.json  (accounts[]: account_number, account_name,
                                account_type_code, entity_code, currency)
      - transaction_index.json (transactions[]: txid, date, account_number,
                                entity_code, amount, transaction_type,
                                category, xero_account_code, description,
                                is_intercompany)

    Every fincosys transaction is a single bank-statement line with no
    natural double-entry counterpart, so we synthesize the offsetting
    split into a per-entity `Imbalance-<category>` placeholder account --
    the same pattern GnuCash's own OFX/QIF importers use for unmatched
    imports (see IMBALANCE_ACCOUNT_TYPE above).
    """
    entities_path = os.path.join(data_dir, "MASTER_ENTITIES.json")
    accounts_path = os.path.join(data_dir, "MASTER_ACCOUNTS.json")
    txn_path = os.path.join(data_dir, "transaction_index.json")

    for path in (entities_path, accounts_path, txn_path):
        if not os.path.isfile(path):
            raise FileNotFoundError(
                "fincosys data file not found: {} "
                "(expected under --data-dir)".format(path)
            )

    entities_doc = _read_json(entities_path)
    accounts_doc = _read_json(accounts_path)
    txn_doc = _read_json(txn_path)

    entities_by_code = OrderedDict()
    for e in entities_doc.get("entities", []):
        code = e.get("code")
        if not code:
            continue
        entities_by_code[code] = e

    accounts = []
    known_account_codes = set()
    for a in accounts_doc.get("accounts", []):
        code = a.get("account_number")
        if not code:
            continue
        entity_code = a.get("entity_code") or "UNKNOWN"
        accounts.append({
            "code": code,
            "name": a.get("account_name") or code,
            "parent_code": None,
            "account_type": map_account_type_code(a.get("account_type_code")),
            "entity_code": entity_code,
            "currency": a.get("currency") or "ZAR",
            "description": a.get("account_type") or "",
        })
        known_account_codes.add(code)

    # entity codes referenced by MASTER_ACCOUNTS.json / transactions but
    # absent from MASTER_ENTITIES.json (seen in practice: "NEW") still
    # need a home in the account tree -- record them so build_plan() can
    # create an entity node for them rather than erroring out.
    entity_codes_seen = set(entities_by_code.keys())
    for a in accounts:
        entity_codes_seen.add(a["entity_code"])

    transactions = []
    imbalance_accounts_needed = OrderedDict()  # (entity, category) -> True
    for t in txn_doc.get("transactions", []):
        txid = t.get("txid")
        account_code = t.get("account_number")
        entity_code = t.get("entity_code") or "UNKNOWN"
        entity_codes_seen.add(entity_code)
        amount = t.get("amount")
        if amount is None:
            amount = 0.0
        amount = float(amount)
        category = t.get("category") or "UNCATEGORIZED"

        offset_code = imbalance_account_code(entity_code, category)
        imbalance_accounts_needed[(entity_code, category)] = True

        transactions.append({
            "txid": txid,
            "date": t.get("date"),
            "description": t.get("description") or "",
            "currency": t.get("currency") or "ZAR",
            "entity_code": entity_code,
            "splits": [
                {
                    "account_code": account_code,
                    "amount": amount,
                    "memo": t.get("counterparty") or "",
                },
                {
                    "account_code": offset_code,
                    "amount": -amount,
                    "memo": "fincosys auto-balance ({})".format(category),
                },
            ],
            "metadata": {
                "is_intercompany": t.get("is_intercompany"),
                "category": category,
                "xero_account_code": t.get("xero_account_code"),
            },
        })

    # materialize the Imbalance-<category> placeholder accounts themselves
    for (entity_code, category) in imbalance_accounts_needed:
        code = imbalance_account_code(entity_code, category)
        accounts.append({
            "code": code,
            "name": imbalance_account_name(category),
            "parent_code": None,
            "account_type": IMBALANCE_ACCOUNT_TYPE,
            "entity_code": entity_code,
            "currency": "ZAR",
            "description": "Auto-generated placeholder for fincosys "
                            "single-sided bank-statement lines with "
                            "category={}".format(category),
        })

    # entities.json is metadata only -- attach legal_name/entity_type where
    # we have it, defaulting to the bare code for entities fincosys's
    # own MASTER_ENTITIES.json doesn't (yet) know about.
    entities_meta = OrderedDict()
    for code in sorted(entity_codes_seen):
        e = entities_by_code.get(code, {})
        entities_meta[code] = {
            "legal_name": e.get("legal_name") or code,
            "entity_type": e.get("entity_type") or "UNKNOWN",
        }

    return accounts, transactions, entities_meta


# ---------------------------------------------------------------------------
# Plan building + validation (pure Python, no `gnucash` import)
# ---------------------------------------------------------------------------

def build_plan(accounts, transactions, entities_meta=None, source=""):
    """Build the in-memory sync plan: a per-entity account tree, the full
    balanced transaction list, and a validation report.
    """
    entities_meta = entities_meta or {}

    account_codes = set()
    accounts_by_entity = defaultdict(list)
    duplicate_account_codes = []
    for a in accounts:
        code = a.get("code")
        if code in account_codes:
            duplicate_account_codes.append(code)
        account_codes.add(code)
        accounts_by_entity[a.get("entity_code") or "UNKNOWN"].append(code)

    seen_txids = set()
    duplicate_txids = []
    unknown_account_refs = []
    unbalanced_transactions = []
    ok_transaction_count = 0
    transactions_by_entity = defaultdict(int)

    for t in transactions:
        txid = t.get("txid")
        if txid in seen_txids:
            duplicate_txids.append(txid)
        else:
            seen_txids.add(txid)

        splits = t.get("splits") or []
        total = 0.0
        splits_ok = True
        for s in splits:
            code = s.get("account_code")
            amount = s.get("amount") or 0.0
            total += float(amount)
            if code not in account_codes:
                unknown_account_refs.append({
                    "txid": txid,
                    "account_code": code,
                })
                splits_ok = False

        balanced = abs(total) <= BALANCE_TOLERANCE
        if not balanced:
            unbalanced_transactions.append({
                "txid": txid,
                "imbalance": total,
            })

        if balanced and splits_ok:
            ok_transaction_count += 1

        transactions_by_entity[t.get("entity_code") or "UNKNOWN"] += 1

    entity_codes = sorted(
        set(accounts_by_entity.keys()) | set(transactions_by_entity.keys())
        | set(entities_meta.keys())
    )
    per_entity_summary = OrderedDict()
    for code in entity_codes:
        meta = entities_meta.get(code, {})
        per_entity_summary[code] = {
            "legal_name": meta.get("legal_name", code),
            "entity_type": meta.get("entity_type", "UNKNOWN"),
            "account_count": len(accounts_by_entity.get(code, [])),
            "transaction_count": transactions_by_entity.get(code, 0),
        }

    validation = {
        "total_accounts": len(accounts),
        "total_transactions": len(transactions),
        "ok_transaction_count": ok_transaction_count,
        "duplicate_account_codes": sorted(set(duplicate_account_codes)),
        "duplicate_txids": sorted(set(duplicate_txids)),
        "unknown_account_refs": unknown_account_refs,
        "unbalanced_transactions": unbalanced_transactions,
        "is_clean": not (
            duplicate_account_codes
            or duplicate_txids
            or unknown_account_refs
            or unbalanced_transactions
        ),
    }

    plan = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "source": source,
        "entities": per_entity_summary,
        "accounts": accounts,
        "transactions": transactions,
        "validation": validation,
    }
    return plan


def load_plan_inputs(feed_path, data_dir):
    """Resolve --feed / --data-dir into (accounts, transactions,
    entities_meta, source_label)."""
    if feed_path:
        accounts, transactions = load_feed(feed_path)
        return accounts, transactions, {}, "feed:{}".format(feed_path)
    if data_dir:
        accounts, transactions, entities_meta = load_fallback(data_dir)
        return (
            accounts,
            transactions,
            entities_meta,
            "fallback-data-dir:{}".format(data_dir),
        )
    raise ValueError("either --feed or --data-dir is required")


def print_summary(plan):
    v = plan["validation"]
    print("fincosys -> GnuCash sync plan")
    print("  source:              {}".format(plan["source"]))
    print("  generated_at:        {}".format(plan["generated_at"]))
    print("  total accounts:      {}".format(v["total_accounts"]))
    print("  total transactions:  {}".format(v["total_transactions"]))
    print("  balanced & valid:    {}".format(v["ok_transaction_count"]))
    print("  duplicate txids:     {}".format(len(v["duplicate_txids"])))
    print("  duplicate accounts:  {}".format(len(v["duplicate_account_codes"])))
    print("  unknown account refs:{}".format(len(v["unknown_account_refs"])))
    print("  unbalanced txns:     {}".format(len(v["unbalanced_transactions"])))
    print("  plan is clean:       {}".format(v["is_clean"]))
    print()
    print("  per-entity breakdown:")
    print("  {:<8} {:<38} {:>10} {:>14}".format(
        "code", "legal_name", "accounts", "transactions"))
    for code, info in plan["entities"].items():
        print("  {:<8} {:<38} {:>10} {:>14}".format(
            code,
            (info["legal_name"] or "")[:38],
            info["account_count"],
            info["transaction_count"],
        ))
    if not v["is_clean"]:
        print()
        print("  WARNING: plan has validation issues -- see --out JSON for "
              "full detail before running --apply")
        for item in v["duplicate_txids"][:10]:
            print("    duplicate txid: {}".format(item))
        for item in v["unknown_account_refs"][:10]:
            print("    unknown account ref: txid={} account_code={}".format(
                item["txid"], item["account_code"]))
        for item in v["unbalanced_transactions"][:10]:
            print("    unbalanced txn: txid={} imbalance={}".format(
                item["txid"], item["imbalance"]))


def run_plan_only(args):
    accounts, transactions, entities_meta, source = load_plan_inputs(
        args.feed, args.data_dir
    )
    plan = build_plan(accounts, transactions, entities_meta, source=source)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, sort_keys=False)

    print_summary(plan)
    print()
    print("plan written to {}".format(args.out))
    return plan


# ---------------------------------------------------------------------------
# Apply mode: replay the plan into a real GnuCash book via the SWIG bindings
# ---------------------------------------------------------------------------

def _import_gnucash():
    try:
        from gnucash import Session, Account, Transaction, Split, \
            GncNumeric, SessionOpenMode
        from gnucash.gnucash_core_c import (
            ACCT_TYPE_ASSET, ACCT_TYPE_LIABILITY, ACCT_TYPE_BANK,
            ACCT_TYPE_EXPENSE, ACCT_TYPE_INCOME, ACCT_TYPE_EQUITY,
            GNC_DENOM_AUTO, GNC_HOW_DENOM_EXACT,
        )
    except ImportError as exc:
        raise ImportError(
            "Could not import the `gnucash` Python module ({}). "
            "--apply requires a GnuCash build with the SWIG Python "
            "bindings compiled and installed/available on PYTHONPATH -- "
            "see bindings/python/README in this repo, and note that "
            "--plan-only does not need this module at all.".format(exc)
        ) from exc

    return {
        "Session": Session,
        "Account": Account,
        "Transaction": Transaction,
        "Split": Split,
        "GncNumeric": GncNumeric,
        "SessionOpenMode": SessionOpenMode,
        "ACCT_TYPE_ASSET": ACCT_TYPE_ASSET,
        "ACCT_TYPE_LIABILITY": ACCT_TYPE_LIABILITY,
        "ACCT_TYPE_BANK": ACCT_TYPE_BANK,
        "ACCT_TYPE_EXPENSE": ACCT_TYPE_EXPENSE,
        "ACCT_TYPE_INCOME": ACCT_TYPE_INCOME,
        "ACCT_TYPE_EQUITY": ACCT_TYPE_EQUITY,
        "GNC_DENOM_AUTO": GNC_DENOM_AUTO,
        "GNC_HOW_DENOM_EXACT": GNC_HOW_DENOM_EXACT,
    }


TOP_LEVEL_ACCOUNT_NAME = "Fincosys Import"

# fincosys txid is stashed in the Transaction's Num field so re-syncs can
# find and skip transactions that were already imported (see README.md,
# "Idempotency").
TXID_NUM_PREFIX = "fincosys:"


def _account_type_const(gnc, account_type):
    mapping = {
        "ASSET": gnc["ACCT_TYPE_ASSET"],
        "LIABILITY": gnc["ACCT_TYPE_LIABILITY"],
        "BANK": gnc["ACCT_TYPE_BANK"],
        "EXPENSE": gnc["ACCT_TYPE_EXPENSE"],
        "INCOME": gnc["ACCT_TYPE_INCOME"],
        "EQUITY": gnc["ACCT_TYPE_EQUITY"],
    }
    return mapping.get((account_type or "").upper(), gnc["ACCT_TYPE_BANK"])


def _find_or_make_child(gnc, book, parent, name, account_type=None,
                         commodity=None):
    for child in parent.get_children():
        Account = gnc["Account"]
        if not isinstance(child, Account):
            child = Account(instance=child)
        if child.GetName() == name:
            return child
    account = gnc["Account"](book)
    account.SetName(name)
    if account_type is not None:
        account.SetType(account_type)
    if commodity is not None:
        account.SetCommodity(commodity)
    parent.append_child(account)
    return account


def _existing_txids_in_account(account):
    """Return the set of fincosys txids already recorded (via the
    Transaction Num field) on splits belonging to `account`."""
    existing = set()
    for split in account.GetSplitList():
        trans = split.GetParent()
        num = trans.GetNum()
        if num and num.startswith(TXID_NUM_PREFIX):
            existing.add(num[len(TXID_NUM_PREFIX):])
    return existing


def apply_plan(plan, book_url):
    gnc = _import_gnucash()
    Session = gnc["Session"]
    Account = gnc["Account"]
    Transaction = gnc["Transaction"]
    Split = gnc["Split"]
    GncNumeric = gnc["GncNumeric"]
    SessionOpenMode = gnc["SessionOpenMode"]

    try:
        session = Session(book_url, SessionOpenMode.SESSION_NORMAL_OPEN)
    except Exception:
        session = Session(book_url, SessionOpenMode.SESSION_NEW_STORE)

    stats = {
        "accounts_created": 0,
        "accounts_existing": 0,
        "transactions_created": 0,
        "transactions_skipped_existing": 0,
        "transactions_skipped_invalid": 0,
    }

    try:
        book = session.book
        root = book.get_root_account()
        commod_table = book.get_table()
        default_currency = commod_table.lookup("ISO4217", "ZAR")

        top = _find_or_make_child(
            gnc, book, root, TOP_LEVEL_ACCOUNT_NAME,
            account_type=gnc["ACCT_TYPE_ASSET"],
        )
        top.SetPlaceholder(True)

        # entity_code -> Account node under "Fincosys Import"
        entity_nodes = {}
        for entity_code, info in plan["entities"].items():
            node = _find_or_make_child(
                gnc, book, top, entity_code,
                account_type=gnc["ACCT_TYPE_ASSET"],
            )
            node.SetPlaceholder(True)
            node.SetDescription(info.get("legal_name", entity_code))
            entity_nodes[entity_code] = node

        # leaf accounts (bank accounts + Imbalance-<category> placeholders)
        account_index = {}
        existing_txids = set()
        for a in plan["accounts"]:
            entity_code = a.get("entity_code") or "UNKNOWN"
            parent = entity_nodes.get(entity_code)
            if parent is None:
                parent = _find_or_make_child(
                    gnc, book, top, entity_code,
                    account_type=gnc["ACCT_TYPE_ASSET"],
                )
                parent.SetPlaceholder(True)
                entity_nodes[entity_code] = parent

            currency = commod_table.lookup(
                "ISO4217", a.get("currency") or "ZAR"
            ) or default_currency

            before = len(list(parent.get_children()))
            account = _find_or_make_child(
                gnc, book, parent, a["name"],
                account_type=_account_type_const(gnc, a.get("account_type")),
                commodity=currency,
            )
            account.SetCode(a["code"])
            if a.get("description"):
                account.SetDescription(a["description"])
            after_is_new = len(list(parent.get_children())) > before
            if after_is_new:
                stats["accounts_created"] += 1
            else:
                stats["accounts_existing"] += 1

            account_index[a["code"]] = account
            existing_txids |= _existing_txids_in_account(account)

        # transactions
        for t in plan["transactions"]:
            if not t.get("splits") or len(t["splits"]) < 2:
                stats["transactions_skipped_invalid"] += 1
                continue

            txid = t.get("txid")
            if txid in existing_txids:
                stats["transactions_skipped_existing"] += 1
                continue

            total = sum(float(s.get("amount") or 0.0) for s in t["splits"])
            if abs(total) > BALANCE_TOLERANCE:
                stats["transactions_skipped_invalid"] += 1
                continue

            missing = [
                s["account_code"] for s in t["splits"]
                if s["account_code"] not in account_index
            ]
            if missing:
                stats["transactions_skipped_invalid"] += 1
                continue

            currency = commod_table.lookup(
                "ISO4217", t.get("currency") or "ZAR"
            ) or default_currency

            trans = Transaction(book)
            trans.BeginEdit()
            trans.SetCurrency(currency)
            trans.SetDescription(t.get("description") or "")
            trans.SetNum(TXID_NUM_PREFIX + str(txid))
            date_str = t.get("date")
            if date_str:
                year, month, day = (int(p) for p in date_str.split("-"))
                trans.SetDate(day, month, year)

            for s in t["splits"]:
                split = Split(book)
                split.SetParent(trans)
                split.SetAccount(account_index[s["account_code"]])
                amount = GncNumeric(round(float(s["amount"]) * 100), 100)
                split.SetValue(amount)
                split.SetAmount(amount)
                if s.get("memo"):
                    split.SetMemo(s["memo"])

            trans.CommitEdit()
            existing_txids.add(txid)
            stats["transactions_created"] += 1

        session.save()
    finally:
        session.end()

    return stats


def run_apply(args):
    if args.feed or args.data_dir:
        accounts, transactions, entities_meta, source = load_plan_inputs(
            args.feed, args.data_dir
        )
        plan = build_plan(accounts, transactions, entities_meta, source=source)
    else:
        with open(args.out, "r", encoding="utf-8") as fh:
            plan = json.load(fh)

    if not plan["validation"]["is_clean"]:
        print("refusing to --apply: plan has validation issues. Run "
              "--plan-only first and review {} for details, or pass "
              "--force to apply anyway.".format(args.out))
        if not args.force:
            return 1

    stats = apply_plan(plan, args.book)
    print("applied fincosys sync plan to {}".format(args.book))
    for key, value in stats.items():
        print("  {:<32} {}".format(key + ":", value))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Sync fincosys financial-forensics data into GnuCash."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--feed", default=None,
        help="Path to a fincosys-atomspace-builder GnuCashSyncExporter "
             "sync feed JSON file.",
    )
    source.add_argument(
        "--data-dir", default=None,
        help="Path to a fincosys/data checkout (fallback loader, used "
             "when no --feed is given). Reads MASTER_ENTITIES.json, "
             "MASTER_ACCOUNTS.json and transaction_index.json.",
    )
    parser.add_argument(
        "--plan-only", action="store_true", default=None,
        help="Build and validate the plan only (default mode; does not "
             "import the `gnucash` module).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply the plan to a real GnuCash book via the `gnucash` "
             "Python bindings. Requires --book.",
    )
    parser.add_argument(
        "--book", default=None,
        help="GnuCash book URL/path to open or create for --apply, e.g. "
             "/path/to/book.gnucash, sqlite3:///path/to/book.gnucash, "
             "postgres://user:pass@host/dbname.",
    )
    parser.add_argument(
        "--out", default="sync_plan.json",
        help="Where to write the plan JSON (--plan-only), or, when "
             "--apply is combined with neither --feed nor --data-dir, "
             "where to read a previously written plan from. "
             "(default: sync_plan.json)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="With --apply, proceed even if the plan has validation "
             "issues (unknown accounts / unbalanced transactions / "
             "duplicate txids are skipped rather than applied).",
    )
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.apply:
        if not args.book:
            parser.error("--apply requires --book")
        if not args.feed and not args.data_dir and not os.path.isfile(args.out):
            parser.error(
                "--apply needs a plan: pass --feed or --data-dir to build "
                "one on the fly, or point --out at a plan JSON file "
                "written by a previous --plan-only run"
            )
        return run_apply(args)

    if not args.feed and not args.data_dir:
        parser.error("one of --feed or --data-dir is required")
    run_plan_only(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
