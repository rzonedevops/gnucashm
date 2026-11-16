#!/usr/bin/env python3
"""Turn Meridian QuickBooks general-ledger captures into a GnuCash sync feed.

fincosys places each QuickBooks company's whole general ledger in the entity
repositories as a *Meridian capture*::

    entity-*/accounting/qbo/meridian/<captured>/
        MANIFEST.json            schema fincosys.qbo_meridian_capture.v1
        general_ledger.csv.gz    one row per posting, every account
        bank_ledger.csv.gz       the same report cut to bank-type accounts
        reports/*.json.gz        Intuit's native GeneralLedger report JSON

``commerce_import.py`` books commerce *documents* (invoices, orders) into
four summary accounts per entity. This books the *ledger itself*: every
QuickBooks account becomes a GnuCash account and every QuickBooks
transaction becomes one GnuCash transaction whose splits are its postings.
The output is the feed shape ``sync_fincosys.py --feed`` plans and applies,
so nothing downstream changes.

What it checks before booking anything
--------------------------------------
* **The capture is the one its manifest describes.** The decompressed CSV's
  SHA-256 and posting count must equal the manifest's; a capture that does
  not is refused, not repaired.
* **Each transaction balances.** A QuickBooks transaction is the postings
  that share ``(txn_type, txn_type_id)`` -- ids are numbered per type, so a
  Deposit 1 and a Journal Entry 1 are different transactions. Debit minus
  credit must net to zero in the company's home currency; one that does not
  is reported and left out.
* **The capture nets to zero.** If it does not, the report says so: the
  capture was cut short (QuickBooks writes its row-cap marker as a data row).
* **One company, one capture.** The same company is placed in several
  repositories (a canonical one and its mirrors). Copies that are
  byte-identical are booked once; copies of the same capture that differ are
  a conflict and that company is not booked. Older captures of a company are
  reported as superseded, not booked a second time.

Account types
-------------
The GL report does not carry an account's type. It is taken, in order, from
an account list the caller names (``--account-types ENT=path``: a QuickBooks
``Account.csv``, an ``accounts.json`` query response, or fincosys-core's
``coa_schema/live_*.tsv``), then from the capture's own repository
(``accounting/qbo/csv/*/Account.csv``, ``raw-json/accounts.json``), then
from the capture's bank ledger (an account in it *is* bank-type), then from
QuickBooks' system account names, then from the Pastel class of a Sage
account number (``8400/000`` -> current asset). Each account records which
of these decided it (``type_basis``); an account none of them decides is
booked as ASSET and counted as unresolved, so a wrong guess is visible.

What it does not do
-------------------
* **Foreign currency.** QuickBooks reports every posting in the company's
  home currency; the foreign currency and rate, where given, are kept in the
  split memo and the transaction metadata. The book is a home-currency book.
* **Edits after import.** The txid is the QuickBooks transaction identity, so
  re-applying is idempotent -- and a transaction QuickBooks has changed since
  is *not* rewritten. Each transaction carries ``content_sha256`` over its
  postings so a later run can say which booked transactions have changed.
* **Commerce overlap.** The general ledger already holds every QuickBooks
  invoice ``commerce_import.py`` books. Applying both feeds for one entity to
  one book double-counts its sales; the report names the entities where that
  would happen.
"""

import argparse
import csv
import glob
import gzip
import hashlib
import io
import json
import os
import re
import sys
from collections import OrderedDict, defaultdict

MANIFEST_SCHEMA = "fincosys.qbo_meridian_capture.v1"
SCHEMA_VERSION = "1.0"
BALANCE_TOLERANCE = 0.005

# QuickBooks AccountType -> sync feed account_type (sync_fincosys maps these
# to GnuCash's ACCT_TYPE_*; it has no receivable/payable types, so those are
# plain ASSET / LIABILITY).
QBO_TYPE_MAP = {
    "bank": "BANK",
    "accounts receivable": "ASSET",
    "other current asset": "ASSET",
    "fixed asset": "ASSET",
    "other asset": "ASSET",
    "accounts payable": "LIABILITY",
    "credit card": "LIABILITY",
    "other current liability": "LIABILITY",
    "long term liability": "LIABILITY",
    "equity": "EQUITY",
    "income": "INCOME",
    "other income": "INCOME",
    "cost of goods sold": "EXPENSE",
    "expense": "EXPENSE",
    "other expense": "EXPENSE",
}

# QuickBooks' own system accounts, by name.
SYSTEM_NAMES = [
    (re.compile(r"accounts receivable", re.I), "ASSET"),
    (re.compile(r"accounts payable", re.I), "LIABILITY"),
    (re.compile(r"opening balance equity|retained earnings", re.I), "EQUITY"),
    (re.compile(r"undeposited funds", re.I), "ASSET"),
]

# Accounts the fincosys QuickBooks programme created after the held account
# lists were taken (Bank Suspense, the clearing and "Payments with <X>"
# accounts, members' loan accounts). They are balance-sheet holding accounts;
# their QuickBooks type is held nowhere, so they are typed as assets *by
# name* and say so.
CLEARING_NAMES = re.compile(
    r"^(bank suspense|own-account transfers clearing|payments with |"
    r"receipts - payer not established|payments - payee not established|"
    r"sars payments|import vat paid|members' loan accounts|collections clearing)",
    re.I)

# QuickBooks' own template account names, which carry no Sage code.
TEMPLATE_NAMES = [
    (re.compile(r"^(sales\b|services$|interest income|other income|"
                r"discounts given)", re.I), "INCOME"),
    (re.compile(r"(charges|fees|subscriptions|expenses?\b|cost of sales|"
                r"purchases|supplies|utilities|insurance|rent or lease|"
                r"repairs|internet|meals|travel)", re.I), "EXPENSE"),
]

NOT_SPECIFIED_ID = "NS"

SAGE_CODE = re.compile(r"^(\d{4})/\d{3}$")


def pastel_class(account_num):
    """The account type a Sage Pastel account number's class implies.

    The ranges are the ones fincosys-core reads Sage ledgers by: 1000 sales,
    2000 cost of sales, 2500 other income, 3000-4999 expense, 5000 equity and
    loans, 6000 fixed assets, 7000 inventory, 8000 current assets, 9000
    current liabilities. Within 5000, members' contribution and retained
    income (5000-5399) are equity and the loan accounts (5400-5999) liability.
    """
    m = SAGE_CODE.match((account_num or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    if 1000 <= n < 2000:
        return "INCOME"
    if 2000 <= n < 2500:
        return "EXPENSE"
    if 2500 <= n < 3000:
        return "INCOME"
    if 3000 <= n < 5000:
        return "EXPENSE"
    if 5000 <= n < 5400:
        return "EQUITY"
    if 5400 <= n < 6000:
        return "LIABILITY"
    if 6000 <= n < 9000:
        return "ASSET"
    if 9000 <= n < 10000:
        return "LIABILITY"
    return None


# ---------------------------------------------------------------------------
# Discovery and verification
# ---------------------------------------------------------------------------

def _repo_of(capture_dir):
    """The entity-repository checkout a capture directory sits in."""
    path = os.path.abspath(capture_dir)
    marker = os.sep + os.path.join("accounting", "qbo", "meridian") + os.sep
    idx = path.find(marker)
    return path[:idx] if idx >= 0 else os.path.dirname(path)


def discover_captures(repos_root):
    """Every Meridian capture under a directory of repository checkouts."""
    pattern = os.path.join(
        repos_root, "*", "accounting", "qbo", "meridian", "*", "MANIFEST.json"
    )
    found = []
    for manifest in sorted(glob.glob(pattern)):
        try:
            with open(manifest, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if data.get("schema") == MANIFEST_SCHEMA:
            found.append(os.path.dirname(manifest))
    return found


def load_capture(capture_dir):
    """Read and verify one capture. Returns a dict, or raises ValueError."""
    with open(os.path.join(capture_dir, "MANIFEST.json"), "r",
              encoding="utf-8") as fh:
        manifest = json.load(fh)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("{}: not a {} manifest".format(
            capture_dir, MANIFEST_SCHEMA))
    gl = manifest.get("general_ledger") or {}
    gl_path = os.path.join(capture_dir, gl.get("file") or "general_ledger.csv.gz")
    with gzip.open(gl_path, "rb") as fh:
        raw = fh.read()
    sha = hashlib.sha256(raw).hexdigest()
    if gl.get("csv_sha256") and sha != gl["csv_sha256"]:
        raise ValueError("{}: general ledger sha256 {} != manifest {}".format(
            capture_dir, sha, gl["csv_sha256"]))
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    for r in rows:
        # QuickBooks reports postings with no account under a section it
        # calls "Not Specified" (zero-amount payment and bill-payment legs).
        if not r.get("account_name_id"):
            r["account_name_id"] = NOT_SPECIFIED_ID
            r["account_name"] = r.get("section") or "Not Specified"
    if gl.get("postings") is not None and len(rows) != gl["postings"]:
        raise ValueError("{}: {} postings != manifest {}".format(
            capture_dir, len(rows), gl["postings"]))

    bank_ids = set()
    bank = manifest.get("bank_ledger") or {}
    for key in (bank.get("per_ledger") or {}):
        bank_ids.add(str(key).split("|", 1)[0])

    return {
        "dir": capture_dir,
        "repo": _repo_of(capture_dir),
        "manifest": manifest,
        "csv_sha256": sha,
        "rows": rows,
        "bank_account_ids": bank_ids,
        "company_key": manifest.get("company_id") or manifest.get("realm_id")
        or manifest.get("company"),
        "captured": str(manifest.get("captured") or os.path.basename(capture_dir)),
    }


def select_captures(captures):
    """Pick one capture per company. Returns (selected, report).

    Newest ``captured`` label wins (``2026-11-11b`` sorts after
    ``2026-11-11``). Byte-identical copies in other repositories are noted;
    copies of the same label that differ make the company a conflict, and
    nothing of it is booked.
    """
    by_company = defaultdict(list)
    for cap in captures:
        by_company[cap["company_key"]].append(cap)

    selected, superseded, copies, conflicts = [], [], [], []
    for company, caps in sorted(by_company.items(), key=lambda kv: str(kv[0])):
        latest = max(c["captured"] for c in caps)
        newest = [c for c in caps if c["captured"] == latest]
        shas = {c["csv_sha256"] for c in newest}
        if len(shas) > 1:
            conflicts.append({
                "company": newest[0]["manifest"].get("company"),
                "captured": latest,
                "dirs": [c["dir"] for c in newest],
            })
            continue
        chosen = sorted(newest, key=lambda c: c["dir"])[0]
        selected.append(chosen)
        for c in newest:
            if c is not chosen:
                copies.append({"kept": chosen["dir"], "copy": c["dir"]})
        for c in caps:
            if c["captured"] != latest:
                superseded.append({"dir": c["dir"], "by": chosen["dir"]})
    return selected, {
        "superseded": superseded,
        "identical_copies": copies,
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Account types
# ---------------------------------------------------------------------------

def read_account_types(path):
    """``{account id: QuickBooks AccountType}`` from one account list."""
    types = {}
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data
        if isinstance(data, dict):
            items = (data.get("QueryResponse") or {}).get("Account") \
                or data.get("Account") or data.get("accounts") or []
        for a in items or []:
            if isinstance(a, dict) and a.get("Id") and a.get("AccountType"):
                types[str(a["Id"])] = a["AccountType"]
        return types
    delimiter = "\t" if path.endswith(".tsv") else ","
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=delimiter):
            if row.get("Id") and row.get("AccountType"):
                types[str(row["Id"])] = row["AccountType"]
    return types


def repo_account_types(repo):
    """Account types from the lists the capture's own repository holds.

    Older lists first, so a later list overrides an account it also types.
    """
    types, used = {}, []
    paths = sorted(glob.glob(os.path.join(
        repo, "accounting", "qbo", "csv", "*", "Account.csv")))
    paths += sorted(glob.glob(os.path.join(
        repo, "accounting", "qbo", "raw-json", "accounts.json")))
    for p in paths:
        try:
            got = read_account_types(p)
        except (OSError, ValueError):
            continue
        if got:
            types.update(got)
            used.append(p)
    return types, used


def resolve_type(account_id, name, account_num, listed, bank_ids):
    """Return (feed account_type, basis)."""
    qbo = listed.get(account_id)
    if qbo and qbo.lower() in QBO_TYPE_MAP:
        return QBO_TYPE_MAP[qbo.lower()], "account list"
    if account_id in bank_ids:
        return "BANK", "bank ledger"
    for rx, kind in SYSTEM_NAMES:
        if rx.search(name or ""):
            return kind, "system name"
    kind = pastel_class(account_num)
    if kind:
        return kind, "pastel class"
    if account_id == NOT_SPECIFIED_ID:
        return "ASSET", "not specified"
    leaf = (name or "").split(":")[-1].strip()
    top = (name or "").split(":")[0].strip()
    if CLEARING_NAMES.match(leaf) or CLEARING_NAMES.match(top):
        return "ASSET", "clearing name"
    for rx, kind in TEMPLATE_NAMES:
        if rx.search(leaf):
            return kind, "template name"
    return "ASSET", "unresolved"


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _money(value):
    value = (value or "").strip()
    return float(value) if value else 0.0


def account_code(entity, account_id):
    return "QBO-{}-{}".format(entity, account_id)


def convert_capture(capture, entity, extra_types=None):
    """One verified capture -> (accounts, transactions, report)."""
    manifest = capture["manifest"]
    currency = manifest.get("currency") or "ZAR"
    company_ref = manifest.get("realm_id") or manifest.get("company_id")

    listed, used = repo_account_types(capture["repo"])
    if extra_types:
        listed = dict(listed)
        listed.update(extra_types)

    # Accounts: one per account_name_id, named by QuickBooks' full path.
    names = OrderedDict()
    nums = {}
    for r in capture["rows"]:
        aid = r["account_name_id"]
        if aid not in names:
            names[aid] = r["account_name"]
            nums[aid] = r["account_num"]
    name_count = defaultdict(int)
    for n in names.values():
        name_count[n.replace(":", " / ")] += 1

    accounts, basis_count = [], defaultdict(int)
    for aid, qname in names.items():
        display = qname.replace(":", " / ")
        if name_count[display] > 1:
            display = "{} [{}]".format(display, aid)
        kind, basis = resolve_type(aid, qname, nums[aid], listed,
                                   capture["bank_account_ids"])
        basis_count[basis] += 1
        accounts.append({
            "code": account_code(entity, aid),
            "name": display,
            "parent_code": None,
            "account_type": kind,
            "entity_code": entity,
            "currency": currency,
            "description": "QuickBooks account {} ({}); type by {}".format(
                aid, qname, basis),
            "metadata": {"qbo_account_id": aid, "qbo_name": qname,
                         "type_basis": basis},
        })

    # Transactions: the postings sharing (txn_type, txn_type_id).
    groups = OrderedDict()
    for r in capture["rows"]:
        groups.setdefault((r["txn_type"], r["txn_type_id"]), []).append(r)

    transactions, unbalanced, multi_date, total = [], [], 0, 0.0
    for (ttype, tid), rows in groups.items():
        splits, net = [], 0.0
        for r in rows:
            amount = round(_money(r["debt_amt"]) - _money(r["credit_amt"]), 2)
            net += amount
            memo = r.get("memo") or ""
            if r.get("currency") and r["currency"] != currency:
                memo = "{} [{} @ {}]".format(memo, r["currency"],
                                             r.get("exch_rate") or "?").strip()
            splits.append({
                "account_code": account_code(entity, r["account_name_id"]),
                "amount": amount,
                "memo": memo,
            })
        total += net
        txid = "QBO-GL:{}:{}:{}:{}".format(entity, company_ref, ttype, tid)
        if abs(net) > BALANCE_TOLERANCE:
            unbalanced.append({"txid": txid, "imbalance": round(net, 2)})
            continue
        # Fold the sub-cent residue of QuickBooks' own rounding into the
        # largest split, so the feed balances to the tolerance sync_fincosys
        # applies. It is at most half a cent and is recorded.
        residue = round(-sum(s["amount"] for s in splits), 6)
        if residue:
            biggest = max(splits, key=lambda s: abs(s["amount"]))
            biggest["amount"] = round(biggest["amount"] + residue, 6)
        dates = sorted({r["tx_date"] for r in rows if r["tx_date"]})
        if len(dates) > 1:
            multi_date += 1
        first = rows[0]
        label = " ".join(p for p in (ttype, first.get("doc_num"),
                                     first.get("name")) if p)
        content = json.dumps(sorted(
            (r["account_name_id"], r["tx_date"], r["debt_amt"],
             r["credit_amt"], r.get("memo") or "") for r in rows))
        foreign = sorted({(r["currency"], r["exch_rate"]) for r in rows
                          if r.get("currency") and r["currency"] != currency})
        transactions.append({
            "txid": txid,
            "date": dates[0] if dates else None,
            "description": label,
            "currency": currency,
            "entity_code": entity,
            "splits": splits,
            "metadata": {
                "source": "quickbooks general ledger (meridian)",
                "qbo_txn_type": ttype,
                "qbo_txn_id": tid,
                "doc_num": first.get("doc_num") or None,
                "name": first.get("name") or None,
                "last_modified": max((r.get("last_mod_date") or "")
                                     for r in rows) or None,
                "content_sha256": hashlib.sha256(
                    content.encode("utf-8")).hexdigest(),
                "foreign": [{"currency": c, "rate": x} for c, x in foreign],
                "rounding_residue": residue or None,
                "capture": capture["captured"],
            },
        })

    bank_check = bank_ledger_check(capture, transactions, entity)

    report = OrderedDict([
        ("capture", capture["dir"]),
        ("company", manifest.get("company")),
        ("entity", entity),
        ("declared_entity", manifest.get("entity")),
        ("captured", capture["captured"]),
        ("currency", currency),
        ("postings", len(capture["rows"])),
        ("accounts", len(accounts)),
        ("account_type_basis", dict(basis_count)),
        ("account_lists_read", used),
        ("transactions", len(transactions)),
        ("unbalanced", unbalanced),
        ("transactions_on_several_dates", multi_date),
        ("capture_net", round(total, 2)),
        ("bank_ledger_check", bank_check),
    ])
    return accounts, transactions, report


def bank_ledger_check(capture, transactions, entity):
    """Each bank account's net in the feed against the bank-ledger report.

    The bank ledger is a second GeneralLedger report QuickBooks produced for
    bank-type accounts only, fetched separately from the full one. Agreement
    per account is evidence the grouping lost or duplicated no posting.
    """
    bank = capture["manifest"].get("bank_ledger") or {}
    path = os.path.join(capture["dir"], bank.get("file") or "bank_ledger.csv.gz")
    if not os.path.exists(path):
        return {"status": "no bank ledger"}
    expected = defaultdict(float)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("account_name_id"):
                expected[r["account_name_id"]] += (
                    _money(r["debt_amt"]) - _money(r["credit_amt"]))
    booked = defaultdict(float)
    prefix = account_code(entity, "")
    for t in transactions:
        for sp in t["splits"]:
            aid = sp["account_code"][len(prefix):]
            if aid in expected:
                booked[aid] += sp["amount"]
    differ = {aid: round(booked[aid] - v, 2) for aid, v in expected.items()
              if abs(booked[aid] - v) > 0.01}
    return {"status": "agrees" if not differ else "differs",
            "accounts": len(expected), "differences": differ}


def convert_root(repos_root=None, capture_dirs=(), aliases=None,
                 extra_types=None):
    """Discover, verify, select and convert. Returns (feed, report)."""
    aliases = aliases or {}
    extra_types = extra_types or {}
    dirs = list(capture_dirs)
    if repos_root:
        dirs += discover_captures(repos_root)
    dirs = sorted(set(os.path.abspath(d) for d in dirs))

    captures, refused = [], []
    for d in dirs:
        try:
            captures.append(load_capture(d))
        except (OSError, ValueError, KeyError) as exc:
            refused.append({"dir": d, "reason": str(exc)})

    selected, selection = select_captures(captures)
    accounts, transactions, reports = [], [], []
    for cap in selected:
        declared = cap["manifest"].get("entity") or "UNKNOWN"
        entity = aliases.get(declared, declared)
        a, t, r = convert_capture(cap, entity, extra_types.get(entity))
        accounts += a
        transactions += t
        reports.append(r)

    feed = {
        "schema_version": SCHEMA_VERSION,
        "source": {"generator": "gnucashm meridian_import.py"},
        "accounts": accounts,
        "transactions": transactions,
    }
    report = {
        "captures_found": len(dirs),
        "refused": refused,
        "selection": selection,
        "books": reports,
    }
    if repos_root:
        report["commerce_overlap"] = commerce_overlap(
            repos_root, {r["entity"] for r in reports}, aliases)
    return feed, report


def commerce_overlap(repos_root, gl_entities, aliases):
    """Entities whose QuickBooks commerce documents the GL already holds."""
    try:
        import commerce_import
    except ImportError:
        return []
    try:
        paths, _skipped = commerce_import.discover_documents(repos_root)
    except Exception:  # discovery is advisory here
        return []
    overlap = set()
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("source") != "quickbooks":
            continue
        code = (doc.get("entity") or {}).get("code")
        code = aliases.get(code, code)
        if code in gl_entities:
            overlap.add(code)
    return sorted(overlap)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_report(report, out=sys.stdout):
    w = out.write
    w("QuickBooks general ledger (Meridian) -> GnuCash feed\n")
    w("=" * 52 + "\n")
    w("captures found: {}\n".format(report["captures_found"]))
    for r in report["refused"]:
        w("  REFUSED {}: {}\n".format(r["dir"], r["reason"]))
    sel = report["selection"]
    for c in sel["conflicts"]:
        w("  CONFLICT {} {}: copies differ, not booked: {}\n".format(
            c["company"], c["captured"], ", ".join(c["dirs"])))
    w("superseded captures: {}; identical copies in other repositories: "
      "{}\n\n".format(len(sel["superseded"]), len(sel["identical_copies"])))
    for b in report["books"]:
        alias = "" if b["entity"] == b["declared_entity"] else \
            " (declared {})".format(b["declared_entity"])
        w("{} {}{} -- {} {}\n".format(b["entity"], b["company"], alias,
                                       b["captured"], b["currency"]))
        w("  postings {}  accounts {}  transactions {}  capture net {}\n"
          .format(b["postings"], b["accounts"], b["transactions"],
                  b["capture_net"]))
        w("  account types by: {}\n".format(", ".join(
            "{} {}".format(k, v) for k, v in sorted(
                b["account_type_basis"].items()))))
        bc = b.get("bank_ledger_check") or {}
        if bc.get("status") == "agrees":
            w("  bank ledger report: {} accounts agree to the cent\n".format(
                bc["accounts"]))
        elif bc.get("status") == "differs":
            w("  BANK LEDGER DIFFERS on {} account(s): {}\n".format(
                len(bc["differences"]), bc["differences"]))
        if b["unbalanced"]:
            w("  UNBALANCED, not booked: {}\n".format(len(b["unbalanced"])))
        if abs(b["capture_net"]) > BALANCE_TOLERANCE:
            w("  WARNING: the capture does not net to zero; it may be cut "
              "short\n")
    if report.get("commerce_overlap"):
        w("\nWARNING: the general ledger already holds the QuickBooks "
          "invoices commerce_import.py books for {}. Applying both feeds "
          "to one book double-counts those sales.\n".format(
              ", ".join(report["commerce_overlap"])))


def build_arg_parser():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("captures", nargs="*",
                   help="Meridian capture directories (holding MANIFEST.json).")
    p.add_argument("--repos-root",
                   help="Directory of entity-repository checkouts to scan.")
    p.add_argument("--master-entities",
                   help="fincosys MASTER_ENTITIES.json (or its directory), "
                        "read for entity-code aliases.")
    p.add_argument("--account-types", action="append", default=[],
                   metavar="ENT=PATH",
                   help="An account list typing ENT's QuickBooks accounts "
                        "(Account.csv, accounts.json, or a coa_schema live "
                        "*.tsv). Repeatable.")
    p.add_argument("--out", help="Write the sync feed here.")
    p.add_argument("--report-json", help="Write the report as JSON here.")
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if not args.captures and not args.repos_root:
        sys.stderr.write("name capture directories or --repos-root\n")
        return 2
    aliases = {}
    if args.master_entities:
        import commerce_import
        aliases, refusals = commerce_import.entity_code_aliases(
            args.master_entities)
        aliases = dict(aliases)
        for r in refusals:
            sys.stderr.write("  alias refused: {}\n".format(r))
    extra = defaultdict(dict)
    for spec in args.account_types:
        ent, _, path = spec.partition("=")
        if not path:
            sys.stderr.write("--account-types wants ENT=PATH, got {}\n"
                             .format(spec))
            return 2
        extra[aliases.get(ent, ent)].update(read_account_types(path))

    feed, report = convert_root(args.repos_root, args.captures, aliases, extra)
    print_report(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(feed, fh, indent=1)
        sys.stdout.write("\nWrote {} accounts and {} transactions to {}\n"
                         .format(len(feed["accounts"]),
                                 len(feed["transactions"]), args.out))
    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
    bad = report["refused"] or report["selection"]["conflicts"] or any(
        b["unbalanced"] or (b.get("bank_ledger_check") or {}).get("status")
        == "differs" for b in report["books"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
