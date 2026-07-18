/********************************************************************\
 * gtest-fincosys-sync.cpp -- Unit tests for the fincosys ecosystem  *
 *                            sync bridge                            *
 *                                                                    *
 * Copyright 2026 GnuCash Contributors                              *
 *                                                                  *
 * This program is free software; you can redistribute it and/or    *
 * modify it under the terms of the GNU General Public License as   *
 * published by the Free Software Foundation; either version 2 of   *
 * the License, or (at your option) any later version.              *
 *                                                                  *
 * This program is distributed in the hope that it will be useful,  *
 * but WITHOUT ANY WARRANTY; without even the implied warranty of   *
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the    *
 * GNU General Public License for more details.                     *
 *                                                                  *
 * You should have received a copy of the GNU General Public License*
 * along with this program; if not, contact:                        *
 *                                                                  *
 * Free Software Foundation           Voice:  +1-617-542-5942       *
 * 51 Franklin Street, Fifth Floor    Fax:    +1-617-542-2652       *
 * Boston, MA  02110-1301,  USA       gnu@gnu.org                   *
 *                                                                  *
\********************************************************************/

#include <config.h>
#include <glib.h>
#include <string>
#include <vector>

#include "../Account.h"
#include "../Split.h"
#include "../Transaction.h"
#include "../gnc-fincosys-sync.h"
#include "../gncOrganization.h"
#include "../qofbook.h"
#include "../qofid.h"

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcpp"
#include <gtest/gtest.h>
#pragma GCC diagnostic pop

class GncFincosysSyncTest : public ::testing::Test
{
protected:
    void SetUp() override
    {
        book = qof_book_new();
    }

    void TearDown() override
    {
        qof_book_destroy(book);
    }

    QofBook* book;
};

namespace
{

/* Collects every GncOrganization currently registered in @a book's
 * GNC_ID_ORGANIZATION collection, in whatever order qof_collection_foreach()
 * visits them. gnc_organizations_from_fincosys_json() only returns a count,
 * not the GncOrganization* pointers it created, so this is how a test
 * recovers the imported organization in order to feed it straight back into
 * gnc_organizations_to_fincosys_json() and exercise a full import-then-export
 * round trip. */
void
collect_organization_cb(QofInstance* inst, gpointer user_data)
{
    auto* orgs = static_cast<std::vector<GncOrganization*>*>(user_data);
    orgs->push_back(GNC_ORGANIZATION(inst));
}

std::vector<GncOrganization*>
collect_organizations(QofBook* book)
{
    std::vector<GncOrganization*> orgs;
    QofCollection* coll = qof_book_get_collection(book, GNC_ID_ORGANIZATION);
    qof_collection_foreach(coll, collect_organization_cb, &orgs);
    return orgs;
}

} // namespace

TEST_F(GncFincosysSyncTest, ExportNullOrganizationsReturnsNull)
{
    gchar* json = gnc_organizations_to_fincosys_json(nullptr);
    EXPECT_EQ(nullptr, json);
}

TEST_F(GncFincosysSyncTest, ExportSingleOrganizationNoAccounts)
{
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetID(org, "RST");
    gncOrganizationSetName(org, "RegimA Skin Treatments CC");
    gncOrganizationSetActive(org, TRUE);

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    std::string text(json);
    EXPECT_NE(std::string::npos,
              text.find("\"schema\": \"fincosys-ecosystem-sync/v1\""));
    EXPECT_NE(std::string::npos, text.find("\"source\": \"gnucashm\""));
    EXPECT_NE(std::string::npos, text.find("\"code\": \"RST\""));
    EXPECT_NE(std::string::npos,
              text.find("\"name\": \"RegimA Skin Treatments CC\""));
    EXPECT_NE(std::string::npos, text.find("\"active\": true"));

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
}

TEST_F(GncFincosysSyncTest, ExportOrganizationWithAccount)
{
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetID(org, "RST");
    gncOrganizationSetName(org, "RegimA Skin Treatments CC");

    Account* account = xaccMallocAccount(book);
    xaccAccountSetName(account, "Bank");
    xaccAccountSetCode(account, "1000");
    xaccAccountSetType(account, ACCT_TYPE_BANK);
    gncOrganizationAddEntity(org, QOF_INSTANCE(account));

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    std::string text(json);
    EXPECT_NE(std::string::npos, text.find("\"account_number\": \"1000\""));
    EXPECT_NE(std::string::npos, text.find("\"account_name\": \"Bank\""));
    EXPECT_NE(std::string::npos, text.find("\"account_type\": \"BANK\""));

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
    xaccAccountDestroy(account);
}

TEST_F(GncFincosysSyncTest, ExportSkipsNonAccountEntities)
{
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetID(org, "RST");

    /* A bare QofInstance (not an Account) should be ignored, not crash. */
    auto* plain = static_cast<QofInstance*>(g_object_new(QOF_TYPE_INSTANCE, NULL));
    plain->e_type = "SomethingElse";
    gncOrganizationAddEntity(org, plain);

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    std::string text(json);
    EXPECT_NE(std::string::npos, text.find("\"accounts\": []"));

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
    g_object_unref(plain);
}

TEST_F(GncFincosysSyncTest, ExportSkipsOrganizationWithoutCode)
{
    /* A freshly-created GncOrganization has no ID until
     * gncOrganizationSetID() is called. Exporting it anyway would produce
     * a record the importer (and the Python loader) can never re-import,
     * since both treat "code" as the primary key. */
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetName(org, "Unidentified Org");

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    std::string text(json);
    EXPECT_EQ(std::string::npos, text.find("Unidentified Org"));
    EXPECT_NE(std::string::npos, text.find("\"organizations\": [\n\n  ]"));

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
}

TEST_F(GncFincosysSyncTest, ExportSkipsAccountWithoutIdentifier)
{
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetID(org, "RST");

    /* No code and no name set -- acct_number resolves to empty. */
    Account* account = xaccMallocAccount(book);
    xaccAccountSetType(account, ACCT_TYPE_BANK);
    gncOrganizationAddEntity(org, QOF_INSTANCE(account));

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    std::string text(json);
    EXPECT_NE(std::string::npos, text.find("\"accounts\": []"));

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
    xaccAccountDestroy(account);
}

TEST_F(GncFincosysSyncTest, ImportNullJsonReturnsMinusOne)
{
    EXPECT_EQ(-1, gnc_organizations_from_fincosys_json(book, nullptr));
}

TEST_F(GncFincosysSyncTest, ImportInvalidJsonReturnsMinusOne)
{
    EXPECT_EQ(-1, gnc_organizations_from_fincosys_json(book, "not json"));
}

TEST_F(GncFincosysSyncTest, ImportEmptyOrganizationsReturnsZero)
{
    const gchar* json =
        R"JSON({"schema":"fincosys-ecosystem-sync/v1","organizations":[]})JSON";
    EXPECT_EQ(0, gnc_organizations_from_fincosys_json(book, json));
}

TEST_F(GncFincosysSyncTest, ImportRejectsTrailingGarbageAfterDocument)
{
    /* Two concatenated JSON documents -- only the first would previously
     * be parsed silently, with the rest ignored. */
    const gchar* json =
        R"JSON({"schema":"fincosys-ecosystem-sync/v1","organizations":[]}{"garbage":true})JSON";
    EXPECT_EQ(-1, gnc_organizations_from_fincosys_json(book, json));
}

TEST_F(GncFincosysSyncTest, ImportRejectsTruncatedTrailingText)
{
    const gchar* json =
        R"JSON({"schema":"fincosys-ecosystem-sync/v1","organizations":[]} trailing text)JSON";
    EXPECT_EQ(-1, gnc_organizations_from_fincosys_json(book, json));
}

TEST_F(GncFincosysSyncTest, ImportCreatesOrganizationAndAccount)
{
    const gchar* json = R"JSON(
    {
      "schema": "fincosys-ecosystem-sync/v1",
      "source": "fincosys-atomspace-builder",
      "organizations": [
        {
          "code": "RST",
          "name": "RegimA Skin Treatments CC",
          "active": true,
          "accounts": [
            {
              "account_number": "1000",
              "account_name": "Bank",
              "account_type": "BANK",
              "balance": 12345.67,
              "currency": "ZAR"
            }
          ]
        }
      ]
    }
    )JSON";

    gint count = gnc_organizations_from_fincosys_json(book, json);
    EXPECT_EQ(1, count);
}

TEST_F(GncFincosysSyncTest, ImportSkipsOrganizationWithoutCode)
{
    const gchar* json = R"JSON(
    {"schema":"fincosys-ecosystem-sync/v1","organizations":[{"name":"No code"}]}
    )JSON";

    EXPECT_EQ(0, gnc_organizations_from_fincosys_json(book, json));
}

TEST_F(GncFincosysSyncTest, RoundTripExportThenImport)
{
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetID(org, "SLG");
    gncOrganizationSetName(org, "Strategic Logistics Group");

    Account* account = xaccMallocAccount(book);
    xaccAccountSetName(account, "Warehouse Stock");
    xaccAccountSetCode(account, "2000");
    xaccAccountSetType(account, ACCT_TYPE_ASSET);
    gncOrganizationAddEntity(org, QOF_INSTANCE(account));

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    QofBook* import_book = qof_book_new();
    gint count = gnc_organizations_from_fincosys_json(import_book, json);
    EXPECT_EQ(1, count);

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
    xaccAccountDestroy(account);
    qof_book_destroy(import_book);
}

/* --- evidence_refs / legal_categories round-trip (see gnc-fincosys-sync.cpp:
 * EVIDENCE_REFS_TAG / LEGAL_CATEGORIES_TAG and their doc comments) --- */

TEST_F(GncFincosysSyncTest, ImportRecordsEvidenceRefsAndLegalCategoriesInNotes)
{
    const gchar* json = R"JSON(
    {
      "schema": "fincosys-ecosystem-sync/v1",
      "source": "fincosys-atomspace-builder",
      "organizations": [
        {
          "code": "RST",
          "name": "RegimA Skin Treatments CC",
          "active": true,
          "evidence_refs": ["JF03-017", "SF10-002"],
          "legal_categories": ["revenue_theft", "trust_violation"],
          "accounts": []
        }
      ]
    }
    )JSON";

    ASSERT_EQ(1, gnc_organizations_from_fincosys_json(book, json));

    std::vector<GncOrganization*> orgs = collect_organizations(book);
    ASSERT_EQ(1U, orgs.size());

    const char* notes = gncOrganizationGetNotes(orgs[0]);
    ASSERT_NE(nullptr, notes);
    std::string notes_str(notes);
    EXPECT_NE(std::string::npos,
              notes_str.find("fincosys:evidence_refs=JF03-017,SF10-002"));
    EXPECT_NE(std::string::npos,
              notes_str.find("fincosys:legal_categories=revenue_theft,trust_violation"));
}

TEST_F(GncFincosysSyncTest, ImportWithoutEvidenceFieldsLeavesNotesUnset)
{
    /* No "evidence_refs"/"legal_categories" keys at all -- the notes field
     * should be left untouched (nullptr on a freshly created organization)
     * rather than acquiring an empty tagged-line stub. */
    const gchar* json = R"JSON(
    {
      "schema": "fincosys-ecosystem-sync/v1",
      "organizations": [
        {"code": "RWD", "name": "RegimA Worldwide Distribution", "active": true}
      ]
    }
    )JSON";

    ASSERT_EQ(1, gnc_organizations_from_fincosys_json(book, json));

    std::vector<GncOrganization*> orgs = collect_organizations(book);
    ASSERT_EQ(1U, orgs.size());

    const char* notes = gncOrganizationGetNotes(orgs[0]);
    EXPECT_TRUE(notes == nullptr || notes[0] == '\0');
}

TEST_F(GncFincosysSyncTest, ImportWithEmptyEvidenceArraysLeavesNotesUnset)
{
    /* Present-but-empty arrays should behave the same as absent ones. */
    const gchar* json = R"JSON(
    {
      "schema": "fincosys-ecosystem-sync/v1",
      "organizations": [
        {
          "code": "VVA",
          "name": "Villa Via Arcadia No 2",
          "active": true,
          "evidence_refs": [],
          "legal_categories": []
        }
      ]
    }
    )JSON";

    ASSERT_EQ(1, gnc_organizations_from_fincosys_json(book, json));

    std::vector<GncOrganization*> orgs = collect_organizations(book);
    ASSERT_EQ(1U, orgs.size());

    const char* notes = gncOrganizationGetNotes(orgs[0]);
    EXPECT_TRUE(notes == nullptr || notes[0] == '\0');
}

TEST_F(GncFincosysSyncTest, ExportEmitsEvidenceRefsAndLegalCategoriesFromTaggedNotes)
{
    /* Mirrors exactly what gnc_organizations_from_fincosys_json() writes
     * into "notes" (see EVIDENCE_REFS_TAG/LEGAL_CATEGORIES_TAG), so this
     * exercises the export-side tagged-line parsing independently of
     * import. */
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetID(org, "RST");
    gncOrganizationSetName(org, "RegimA Skin Treatments CC");
    gncOrganizationSetNotes(org,
        "Synced from fincosys-ecosystem-sync/v1:\n"
        "fincosys:evidence_refs=JF03-017,SF10-002\n"
        "fincosys:legal_categories=revenue_theft,trust_violation");

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    std::string text(json);
    EXPECT_NE(std::string::npos,
              text.find("\"evidence_refs\": [\"JF03-017\", \"SF10-002\"]"));
    EXPECT_NE(std::string::npos,
              text.find("\"legal_categories\": [\"revenue_theft\", \"trust_violation\"]"));

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
}

TEST_F(GncFincosysSyncTest, ExportEmitsEmptyArraysWhenNoEvidenceTagsInNotes)
{
    GncOrganization* org = gncOrganizationCreate(book);
    gncOrganizationSetID(org, "RST");
    gncOrganizationSetName(org, "RegimA Skin Treatments CC");
    /* No notes set at all -- export must still emit the (empty) arrays
     * rather than omitting the keys. */

    GList* orgs = g_list_append(nullptr, org);
    gchar* json = gnc_organizations_to_fincosys_json(orgs);
    ASSERT_NE(nullptr, json);

    std::string text(json);
    EXPECT_NE(std::string::npos, text.find("\"evidence_refs\": []"));
    EXPECT_NE(std::string::npos, text.find("\"legal_categories\": []"));

    g_free(json);
    g_list_free(orgs);
    gncOrganizationDestroy(org);
}

TEST_F(GncFincosysSyncTest,
       RoundTripPreservesEvidenceRefsAndLegalCategoriesThroughImportThenExport)
{
    /* The core round trip this PR adds: a sync-schema document with
     * populated evidence_refs/legal_categories, imported via
     * gnc_organizations_from_fincosys_json() (stored as tagged "notes"
     * lines, since GncOrganization has no generic KVP accessor), then
     * exported again via gnc_organizations_to_fincosys_json() -- the
     * arrays must come back exactly as they went in. */
    const gchar* json = R"JSON(
    {
      "schema": "fincosys-ecosystem-sync/v1",
      "source": "fincosys-atomspace-builder",
      "organizations": [
        {
          "code": "SLG",
          "name": "Strategic Logistics Group",
          "active": true,
          "evidence_refs": ["JF03-017", "SF10-002", "SF15-001"],
          "legal_categories": ["stock_disappearance", "trust_violation"],
          "accounts": []
        }
      ]
    }
    )JSON";

    ASSERT_EQ(1, gnc_organizations_from_fincosys_json(book, json));

    std::vector<GncOrganization*> orgs = collect_organizations(book);
    ASSERT_EQ(1U, orgs.size());

    GList* org_list = g_list_append(nullptr, orgs[0]);
    gchar* exported = gnc_organizations_to_fincosys_json(org_list);
    ASSERT_NE(nullptr, exported);

    std::string text(exported);
    EXPECT_NE(std::string::npos,
              text.find("\"evidence_refs\": [\"JF03-017\", \"SF10-002\", \"SF15-001\"]"));
    EXPECT_NE(std::string::npos,
              text.find("\"legal_categories\": [\"stock_disappearance\", \"trust_violation\"]"));

    g_free(exported);
    g_list_free(org_list);
}

TEST_F(GncFincosysSyncTest, TxSyncNullJsonReturnsMinusOne)
{
    EXPECT_EQ(-1, gnc_transactions_from_syncfeed_json(book, nullptr));
}

TEST_F(GncFincosysSyncTest, TxSyncInvalidJsonReturnsMinusOne)
{
    EXPECT_EQ(-1, gnc_transactions_from_syncfeed_json(book, "not json"));
}

TEST_F(GncFincosysSyncTest, TxSyncMissingTransactionsArrayReturnsZero)
{
    const gchar* json = R"JSON({"schema_version":"1.0","accounts":[]})JSON";
    EXPECT_EQ(0, gnc_transactions_from_syncfeed_json(book, json));
}

TEST_F(GncFincosysSyncTest, TxSyncCreatesBalancedTransaction)
{
    const gchar* json = R"JSON(
    {
      "schema_version": "1.0",
      "source": {"repo": "fincosys", "generator": "fincosys-atomspace-builder"},
      "accounts": [
        {"code": "RST", "name": "RST", "parent_code": null, "account_type": "EQUITY", "currency": "ZAR"},
        {"code": "1000", "name": "Bank", "parent_code": "RST", "account_type": "BANK", "currency": "ZAR"},
        {"code": "RST-Imbalance-UNCATEGORIZED", "name": "Imbalance", "parent_code": "RST", "account_type": "EXPENSE", "currency": "ZAR"}
      ],
      "transactions": [
        {
          "txid": "TX001",
          "date": "2026-01-15",
          "description": "Test payment",
          "currency": "ZAR",
          "entity_code": "RST",
          "splits": [
            {"account_code": "1000", "amount": -500.0, "memo": "out"},
            {"account_code": "RST-Imbalance-UNCATEGORIZED", "amount": 500.0, "memo": "out"}
          ]
        }
      ]
    }
    )JSON";

    gint count = gnc_transactions_from_syncfeed_json(book, json);
    EXPECT_EQ(1, count);

    Account* bank = gnc_account_lookup_by_code(gnc_book_get_root_account(book), "1000");
    ASSERT_NE(nullptr, bank);
    EXPECT_STREQ("Bank", xaccAccountGetName(bank));

    SplitList* splits = xaccAccountGetSplitList(bank);
    ASSERT_NE(nullptr, splits);
    Split* split = static_cast<Split*>(splits->data);
    Transaction* trans = xaccSplitGetParent(split);
    ASSERT_NE(nullptr, trans);
    EXPECT_STREQ("Test payment", xaccTransGetDescription(trans));
    EXPECT_STREQ("TX001", xaccTransGetNum(trans));
    EXPECT_EQ(2, xaccTransCountSplits(trans));
}

TEST_F(GncFincosysSyncTest, TxSyncSkipsSplitWithUnknownAccountCode)
{
    const gchar* json = R"JSON(
    {
      "schema_version": "1.0",
      "accounts": [
        {"code": "1000", "name": "Bank", "account_type": "BANK", "currency": "ZAR"}
      ],
      "transactions": [
        {
          "txid": "TX002",
          "date": "2026-01-16",
          "description": "Unmatched split",
          "currency": "ZAR",
          "splits": [
            {"account_code": "1000", "amount": -10.0, "memo": ""},
            {"account_code": "DOES-NOT-EXIST", "amount": 10.0, "memo": ""}
          ]
        }
      ]
    }
    )JSON";

    gint count = gnc_transactions_from_syncfeed_json(book, json);
    EXPECT_EQ(1, count);

    Account* bank = gnc_account_lookup_by_code(gnc_book_get_root_account(book), "1000");
    ASSERT_NE(nullptr, bank);
    SplitList* splits = xaccAccountGetSplitList(bank);
    ASSERT_NE(nullptr, splits);
    Split* split = static_cast<Split*>(splits->data);
    Transaction* trans = xaccSplitGetParent(split);
    ASSERT_NE(nullptr, trans);
    EXPECT_EQ(1, xaccTransCountSplits(trans));
}
