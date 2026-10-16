"""Tests for the commerce wiring in scripts/sync_fincosys_ecosystem.py.

The script lives in `scripts/`, not in this package; it is loaded by path
here for the same reason it loads `commerce_import.py` by path -- importing
either through `bindings/python/` pulls in the compiled `gnucash` extension
as a side effect, which these tests do not need and which is usually absent
in the environment they run in.
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPT = REPO_ROOT / "scripts" / "sync_fincosys_ecosystem.py"


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("ecosystem_sync_driver", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _document(tmp_path, name, *, code, source="quickbooks", complete=True,
              generated_at="2026-01-01T00:00:00Z", record_ids=(),
              repository="fincosys/entity-x", records=None):
    payload = {
        "schema": "fincosys-commerce-sync/v1",
        "source": source,
        "generated_at": generated_at,
        "entity": {"code": code, "legal_name": "X", "repository": repository},
        "window": {"from": "2020-01-01", "to": "2020-12-31",
                   "basis": "TxnDate", "complete": complete},
    }
    if records is None:
        records = [
            {"record_id": rid, "record_type": "sales_invoice", "currency": "ZAR",
             "amounts": {"subtotal": "100", "shipping": "0", "tax": "0",
                         "total": "100"}}
            for rid in record_ids
        ]
    if records is not False:
        payload["records"] = records
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


# -- selection -----------------------------------------------------------

def test_a_report_capture_is_skipped_not_loaded(driver, tmp_path):
    """Several QuickBooks documents declare this schema and hold a report.

    They are not record documents and not errors. Handing one to the loader
    would have it read a balance sheet as a sales ledger.
    """
    repo = tmp_path / "entity-x"
    (repo / "accounting" / "qbo" / "reports").mkdir(parents=True)
    records = _document(repo / "accounting/qbo/reports", "recs.json",
                        code="RST", record_ids=["QBO_RST_INVOICE_1"])
    report = repo / "accounting/qbo/reports/report.json"
    report.write_text(json.dumps({
        "schema": "fincosys-commerce-sync/v1",
        "entity": {"code": "RST", "repository": "fincosys/entity-x"},
        "report": {"name": "Balance Sheet", "rows": []},
    }))

    paths, skipped = driver.select_commerce_documents(None, str(tmp_path))

    assert [Path(p).name for p in paths] == [records.name]
    assert [Path(p).name for p in skipped] == [report.name]


def test_a_complete_capture_is_ordered_before_a_partial_one(driver, tmp_path):
    """The loader adds a record once and takes the first document to carry it.

    It walks its configured paths in sorted order, so without this the
    winner is decided by filename -- and a partial capture whose name sorts
    earlier would pin every shared record at the partial_capture tier.
    """
    partial = _document(tmp_path, "aaa_partial.json", code="RST",
                        complete=False, record_ids=["QBO_RST_INVOICE_1"])
    complete = _document(tmp_path, "zzz_complete.json", code="RST",
                         complete=True, record_ids=["QBO_RST_INVOICE_1"])

    paths, _ = driver.select_commerce_documents([str(partial), str(complete)], None)

    assert [Path(p).name for p in paths] == [complete.name, partial.name]


def test_among_equals_the_later_capture_is_ordered_first(driver, tmp_path):
    older = _document(tmp_path, "zzz_older.json", code="RST",
                      generated_at="2026-01-01T00:00:00Z",
                      record_ids=["QBO_RST_INVOICE_1"])
    newer = _document(tmp_path, "aaa_newer.json", code="RST",
                      generated_at="2026-09-01T00:00:00Z",
                      record_ids=["QBO_RST_INVOICE_1"])

    paths, _ = driver.select_commerce_documents([str(older), str(newer)], None)

    assert [Path(p).name for p in paths] == [newer.name, older.name]


def test_a_document_named_and_also_discovered_is_listed_once(driver, tmp_path):
    repo = tmp_path / "entity-x"
    target = repo / "accounting" / "qbo" / "reports"
    target.mkdir(parents=True)
    doc = _document(target, "recs.json", code="RST",
                    record_ids=["QBO_RST_INVOICE_1"])

    paths, _ = driver.select_commerce_documents([str(doc)], str(tmp_path))

    assert len(paths) == 1


def test_a_report_block_wins_over_a_records_array(driver, tmp_path):
    """One capture in the corpus carries both, and it is not a record document.

    entity-regima-dr-h-uk's `2026-09-08_ap_aging_detail` declares
    `report: "ap_aging_detail"` and puts its 339 aging rows under `records`
    -- rows with a row_id, an aging_bucket and days_past_due, and no
    record_id, record_type or currency. Read as a record document it is
    simply malformed, and it stopped a corpus-wide run over one file.

    It is not fixed at the source because that capture is SHA-256-sealed in
    its repository's manifest, so the consumer recognises it instead.
    """
    repo = tmp_path / "entity-x"
    target = repo / "accounting" / "qbo" / "reports"
    target.mkdir(parents=True)

    good = _document(target, "invoices.json", code="RST",
                     record_ids=["QBO_RST_INVOICE_1"])
    mislabelled = target / "ap_aging_detail.json"
    mislabelled.write_text(json.dumps({
        "schema": "fincosys-commerce-sync/v1",
        "report": "ap_aging_detail",
        "entity": {"entity_code": "DRH", "name": "Regima @ Dr H Ltd"},
        "records": [
            {"row_id": "1.1", "aging_bucket": "91 or more days past due",
             "vendor": "Pro Beauty Show (V)", "days_past_due": 4579,
             "amount": "35.00", "open_balance": "35.00"},
        ],
    }))

    paths, skipped = driver.select_commerce_documents(None, str(tmp_path))

    assert [Path(p).name for p in paths] == [good.name]
    assert [Path(p).name for p in skipped] == [mislabelled.name]


# -- entity-code audit ---------------------------------------------------

def test_an_uncanonical_entity_code_is_reported(driver, tmp_path):
    """A code no entity node carries orphans every record in the document.

    CommerceLoader._link drops an edge whose entity node is absent rather
    than inventing one, so the records are created and attach to nothing.
    """
    doc = _document(tmp_path, "d.json", code="DRH",
                    record_ids=["QBO_DRH_INVOICE_1"])

    audit = driver.audit_entity_codes([str(doc)], {"RDH", "RST"})

    assert list(audit["unknown_codes"]) == ["DRH"]
    assert audit["unknown_codes"]["DRH"] == [str(doc)]


def test_a_canonical_code_is_not_reported(driver, tmp_path):
    doc = _document(tmp_path, "d.json", code="RDH",
                    record_ids=["QBO_RDH_INVOICE_1"])

    audit = driver.audit_entity_codes([str(doc)], {"RDH", "RST"})

    assert audit["unknown_codes"] == {}
    assert audit["aliased_entities"] == []


def test_two_codes_for_one_entity_are_reported_with_the_overlap(driver, tmp_path):
    """Record ids embed the code, so no de-duplication can see these as one."""
    a = _document(tmp_path, "a.json", code="RDH",
                  repository="fincosys/entity-regima-dr-h-uk",
                  record_ids=["QBO_RDH_INVOICE_1", "QBO_RDH_INVOICE_2",
                              "QBO_RDH_INVOICE_3"])
    b = _document(tmp_path, "b.json", code="DRH",
                  repository="fincosys/entity-regima-dr-h-uk",
                  record_ids=["QBO_DRH_INVOICE_1", "QBO_DRH_INVOICE_2"])

    audit = driver.audit_entity_codes([str(a), str(b)], {"RDH", "DRH"})

    assert len(audit["aliased_entities"]) == 1
    entry = audit["aliased_entities"][0]
    assert entry["anchor"] == "fincosys/entity-regima-dr-h-uk"
    assert entry["codes"] == ["DRH", "RDH"]
    assert entry["documents_carried_twice"] == 2
    assert entry["records_per_code"] == {"RDH": 3, "DRH": 2}


def test_the_same_invoice_number_in_two_entities_is_not_a_collision(driver, tmp_path):
    """A provider's document numbering is scoped to the provider account.

    QBO_RZI_INVOICE_100 and QBO_RZL_INVOICE_100 are two unrelated invoices
    in two different QuickBooks companies. Keying the collision test on the
    record id alone reports every such pair -- 903 of them across this
    corpus, against one real finding.
    """
    a = _document(tmp_path, "a.json", code="RZI",
                  repository="fincosys/entity-rzi",
                  record_ids=["QBO_RZI_INVOICE_100"])
    b = _document(tmp_path, "b.json", code="RZL",
                  repository="fincosys/entity-rzl",
                  record_ids=["QBO_RZL_INVOICE_100"])

    audit = driver.audit_entity_codes([str(a), str(b)], {"RZI", "RZL"})

    assert audit["aliased_entities"] == []


def test_the_realm_is_matched_under_either_spelling(driver, tmp_path):
    """The corpus spells one field two ways, and that splits an entity.

    `provider.realm_id` and `provider.realm` are the same field. Keying on
    one of them puts an entity's documents under two anchors, which reads as
    no collision -- the failure this pass exists to catch.
    """
    def write(name, code, provider):
        payload = json.loads(
            _document(tmp_path, name, code=code, repository=None,
                      record_ids=["QBO_%s_INVOICE_1" % code]).read_text())
        payload["entity"].pop("repository", None)
        payload["provider"] = provider
        path = tmp_path / name
        path.write_text(json.dumps(payload))
        return path

    a = write("a.json", "RDH", {"name": "quickbooks", "realm_id": "1366568670"})
    b = write("b.json", "DRH", {"name": "quickbooks", "realm": "1366568670"})

    audit = driver.audit_entity_codes([str(a), str(b)], {"RDH", "DRH"})

    assert len(audit["aliased_entities"]) == 1
    assert audit["aliased_entities"][0]["anchor"] == "1366568670"
    assert audit["aliased_entities"][0]["documents_carried_twice"] == 1


def test_unreadable_master_data_suppresses_the_unknown_code_report(driver, tmp_path):
    """None is not an empty set.

    Without the master data there is no list of canonical codes, and
    treating that as "no codes are canonical" would report every document in
    the corpus as unknown -- noise that would bury the one real finding.
    """
    assert driver.known_entity_codes(str(tmp_path / "nonexistent")) is None

    doc = _document(tmp_path, "d.json", code="ANYTHING",
                    record_ids=["QBO_ANYTHING_INVOICE_1"])
    audit = driver.audit_entity_codes([str(doc)], None)

    assert audit["unknown_codes"] == {}
