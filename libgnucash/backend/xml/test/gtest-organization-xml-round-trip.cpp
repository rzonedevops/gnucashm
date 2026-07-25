/********************************************************************\
 * gtest-organization-xml-round-trip.cpp -- verify that GncOrganization *
 *   records (added by gnc-organization-xml-v2.cpp) survive a real     *
 *   XML book save/reload, not just the accounts they contain.         *
 *                                                                      *
 * Copyright (C) 2026 GnuCash Contributors                             *
 *                                                                      *
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
#include <glib.h>
#include <glib/gstdio.h>

#include <config.h>
#include <unistd.h>
#include <memory>
#include <string>
#include <vector>

#include <cashobjects.h>
#include <TransLog.h>
#include <gnc-engine.h>
#include <gnc-uri-utils.h>

#include <Account.h>
#include <gnc-commodity.h>
#include <gncAddress.h>
#include <gncOrganization.h>
#include <qofid.h>

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcpp"
#include <gtest/gtest.h>
#pragma GCC diagnostic pop
#include <unittest-support.h>

#include "../gnc-backend-xml.h"
#include "../io-gncxml-v2.h"

#define GNC_LIB_NAME "gncmod-backend-xml"
#define GNC_LIB_REL_PATH "xml"

namespace
{

/* Mirrors the note-encoding gnc_organizations_from_fincosys_json() uses in
 * libgnucash/engine/gnc-fincosys-sync.cpp: evidence_refs/legal_categories
 * round-tripped as tagged lines within the organization's free-text notes
 * field, since GncOrganization has no generic KVP accessor of its own. */
const char* kNotes =
    "Synced from fincosys-ecosystem-sync/v1:\n"
    "fincosys:evidence_refs=JF03,SF10\n"
    "fincosys:legal_categories=trust_violation,revenue_theft";

std::vector<GncOrganization*>
collect_organizations (QofBook* book)
{
    std::vector<GncOrganization*> orgs;
    QofCollection* coll = qof_book_get_collection (book, GNC_ID_ORGANIZATION);
    qof_collection_foreach (coll,
        [] (QofInstance* inst, gpointer user_data)
        {
            static_cast<std::vector<GncOrganization*>*> (user_data)
                ->push_back (GNC_ORGANIZATION (inst));
        },
        &orgs);
    return orgs;
}

} // namespace

class OrganizationXmlRoundTrip : public testing::Test
{
public:
    static void SetUpTestSuite ()
    {
        g_setenv ("GNC_UNINSTALLED", "1", TRUE);
        qof_init ();
        cashobjects_register ();
        ASSERT_TRUE (qof_load_backend_library (GNC_LIB_REL_PATH, GNC_LIB_NAME))
            << "loading gnc-backend-xml GModule failed";
        xaccLogDisable ();
    }

    static void TearDownTestSuite () { qof_close (); }
};

#define QOF_SESSION_CHECKED_CALL(_function, _session, ...) \
    do { \
        _function (_session.get (), ## __VA_ARGS__); \
        ASSERT_EQ (qof_session_get_error (_session.get ()), 0) << #_function \
            << ": " << qof_session_get_error (_session.get ()) \
            << " \"" << qof_session_get_error_message (_session.get ()) << "\""; \
    } while (0)

TEST_F (OrganizationXmlRoundTrip, SurvivesSaveAndReload)
{
    gchar* filename1 = g_build_filename (g_get_tmp_dir (),
                                         "test_organization_xml_XXXXXX",
                                         nullptr);
    int fd = g_mkstemp (filename1);
    ASSERT_GE (fd, 0);
    close (fd);
    g_unlink (filename1);
    std::string filename = filename1;
    g_free (filename1);

    auto* url = gnc_uri_normalize_uri (filename.c_str (), FALSE);
    GncGUID org_guid;
    GncGUID account_guid;

    /* --- Build a book with one organization (with an Account entity, an
     * address, a currency and evidence-carrying notes) and save it. --- */
    {
        auto save_session = std::shared_ptr<QofSession> {
            qof_session_new (qof_book_new ()), qof_session_destroy};
        QofBook* book = qof_session_get_book (save_session.get ());

        gnc_commodity* zar = gnc_commodity_table_lookup (
            gnc_commodity_table_get_table (book), GNC_COMMODITY_NS_CURRENCY, "ZAR");
        ASSERT_NE (nullptr, zar);

        Account* account = xaccMallocAccount (book);
        xaccAccountSetName (account, "RST Intercompany");
        xaccAccountSetCode (account, "62012990132");
        xaccAccountSetType (account, ACCT_TYPE_BANK);
        xaccAccountSetCommodity (account, zar);
        gnc_account_append_child (gnc_book_get_root_account (book), account);
        account_guid = *xaccAccountGetGUID (account);

        GncOrganization* org = gncOrganizationCreate (book);
        gncOrganizationSetID (org, "RST");
        gncOrganizationSetName (org, "RegimA Skin Treatments CC");
        gncOrganizationSetNotes (org, kNotes);
        gncOrganizationSetActive (org, TRUE);
        gncOrganizationSetCurrency (org, zar);

        GncAddress* addr = gncAddressCreate (book, QOF_INSTANCE (org));
        gncAddressSetName (addr, "RegimA Skin Treatments CC");
        gncAddressSetAddr1 (addr, "1 Case Evidence Way");
        gncOrganizationSetAddr (org, addr);

        gncOrganizationAddEntity (org, QOF_INSTANCE (account));

        org_guid = *qof_instance_get_guid (QOF_INSTANCE (org));

        ASSERT_EQ (1u, gncOrganizationGetEntityCount (org));

        QOF_SESSION_CHECKED_CALL (qof_session_begin, save_session, url,
                                  SESSION_NEW_OVERWRITE);
        qof_book_mark_session_dirty (book);
        QOF_SESSION_CHECKED_CALL (qof_session_save, save_session, nullptr);
        qof_session_end (save_session.get ());
    }

    /* --- Reload into a completely fresh QofBook/session and check that
     * the organization -- not just the account it references -- survived,
     * per the "Known remaining gap" note in
     * docs/FINCOSYS_ECOSYSTEM_SYNC.md this change closes. --- */
    {
        auto load_session = std::shared_ptr<QofSession> {
            qof_session_new (qof_book_new ()), qof_session_destroy};

        QOF_SESSION_CHECKED_CALL (qof_session_begin, load_session, url,
                                  SESSION_READ_ONLY);
        QOF_SESSION_CHECKED_CALL (qof_session_load, load_session, nullptr);

        QofBook* book = qof_session_get_book (load_session.get ());
        std::vector<GncOrganization*> orgs = collect_organizations (book);
        ASSERT_EQ (1u, orgs.size ());

        GncOrganization* org = orgs[0];
        EXPECT_STREQ ("RST", gncOrganizationGetID (org));
        EXPECT_STREQ ("RegimA Skin Treatments CC", gncOrganizationGetName (org));
        EXPECT_TRUE (gncOrganizationGetActive (org));
        ASSERT_NE (nullptr, gncOrganizationGetNotes (org));
        EXPECT_STREQ (kNotes, gncOrganizationGetNotes (org));
        EXPECT_TRUE (guid_equal (&org_guid,
                                 qof_instance_get_guid (QOF_INSTANCE (org))));

        ASSERT_NE (nullptr, gncOrganizationGetCurrency (org));
        EXPECT_STREQ ("ZAR", gnc_commodity_get_mnemonic (gncOrganizationGetCurrency (org)));

        ASSERT_NE (nullptr, gncOrganizationGetAddr (org));
        EXPECT_STREQ ("RegimA Skin Treatments CC",
                     gncAddressGetName (gncOrganizationGetAddr (org)));
        EXPECT_STREQ ("1 Case Evidence Way",
                     gncAddressGetAddr1 (gncOrganizationGetAddr (org)));

        ASSERT_EQ (1u, gncOrganizationGetEntityCount (org));
        GList* entities = gncOrganizationGetEntities (org);
        ASSERT_NE (nullptr, entities);
        auto* reloaded_account = static_cast<QofInstance*> (entities->data);
        ASSERT_TRUE (GNC_IS_ACCOUNT (reloaded_account));
        EXPECT_TRUE (guid_equal (&account_guid,
                                 qof_instance_get_guid (reloaded_account)));
        EXPECT_STREQ ("RST Intercompany",
                     xaccAccountGetName (GNC_ACCOUNT (reloaded_account)));

        qof_session_end (load_session.get ());
    }

    g_unlink (filename.c_str ());
    g_free (url);
}
