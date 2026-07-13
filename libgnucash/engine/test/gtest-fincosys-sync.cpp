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

#include "../Account.h"
#include "../gnc-fincosys-sync.h"
#include "../gncOrganization.h"
#include "../qofbook.h"

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
