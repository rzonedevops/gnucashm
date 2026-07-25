/********************************************************************\
 * gnc-organization-xml-v2.cpp -- organization xml i/o implementation *
 *                                                                    *
 * Copyright (C) 2026 GnuCash Contributors                           *
 *                                                                    *
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

#include <config.h>
#include <stdlib.h>
#include <string.h>
#include "gncOrganizationP.h"

#include "gnc-xml-helper.h"
#include "sixtp.h"
#include "sixtp-utils.h"
#include "sixtp-parsers.h"
#include "sixtp-utils.h"
#include "sixtp-dom-parsers.h"
#include "sixtp-dom-generators.h"

#include "gnc-xml.h"
#include "io-gncxml-gen.h"
#include "io-gncxml-v2.h"

#include "gnc-organization-xml-v2.h"
#include "gnc-address-xml-v2.h"
#include "xml-helpers.h"

#define _GNC_MOD_NAME   GNC_ID_ORGANIZATION

static QofLogModule log_module = GNC_MOD_IO;

const gchar* organization_version_string = "2.0.0";

/* ids */
#define gnc_organization_string "gnc:GncOrganization"
#define organization_name_string "organization:name"
#define organization_guid_string "organization:guid"
#define organization_id_string "organization:id"
#define organization_addr_string "organization:addr"
#define organization_notes_string "organization:notes"
#define organization_currency_string "organization:currency"
#define organization_active_string "organization:active"
#define organization_entities_string "organization:entities"
#define organization_entity_string "organization:entity"
#define organization_entity_guid_string "organization:entity-guid"
#define organization_entity_qof_type_string "qof-type"
#define organization_slots_string "organization:slots"

/* Each member entity is written as an "organization:entity" node carrying
 * a "qof-type" attribute (the entity's QofInstance::e_type, e.g. "Account")
 * plus a nested "organization:entity-guid" child -- the type is needed on
 * read-back because gncOrganizationGetEntities() returns arbitrary
 * QofInstance* (see gncOrganization.h), and there is no generic
 * guid-to-instance lookup across all QOF types without first knowing which
 * collection to search. In every producer of this data known at the time
 * of writing (gnc-fincosys-sync.cpp), member entities are always Account
 * instances, but this format is not restricted to that. */
static void
add_organization_entities (xmlNodePtr node, GncOrganization* org)
{
    GList* n;
    xmlNodePtr entities_node;

    entities_node = xmlNewChild (node, NULL,
                                 BAD_CAST organization_entities_string, NULL);

    for (n = gncOrganizationGetEntities (org); n; n = n->next)
    {
        QofInstance* entity = static_cast<decltype (entity)> (n->data);
        xmlNodePtr entity_node;

        if (!entity)
            continue;

        entity_node = xmlNewNode (NULL, BAD_CAST organization_entity_string);
        xmlSetProp (entity_node, BAD_CAST organization_entity_qof_type_string,
                    BAD_CAST QOF_INSTANCE (entity)->e_type);
        xmlAddChild (entity_node,
                     guid_to_dom_tree (organization_entity_guid_string,
                                       qof_instance_get_guid (entity)));
        xmlAddChild (entities_node, entity_node);
    }
}

static xmlNodePtr
organization_dom_tree_create (GncOrganization* organization)
{
    xmlNodePtr ret;
    gnc_commodity* currency;

    ret = xmlNewNode (NULL, BAD_CAST gnc_organization_string);
    xmlSetProp (ret, BAD_CAST "version", BAD_CAST organization_version_string);

    xmlAddChild (ret, guid_to_dom_tree (organization_guid_string,
                                        qof_instance_get_guid (QOF_INSTANCE (organization))));

    xmlAddChild (ret, text_to_dom_tree (organization_name_string,
                                        gncOrganizationGetName (organization)));

    xmlAddChild (ret, text_to_dom_tree (organization_id_string,
                                        gncOrganizationGetID (organization)));

    /* gnc_address_to_dom_tree() and its getters are NULL-safe -- a freshly
     * created organization has no address yet (unlike GncVendor, which
     * auto-creates one), so this yields an (empty) addr node rather than
     * crashing. */
    xmlAddChild (ret, gnc_address_to_dom_tree (organization_addr_string,
                                               gncOrganizationGetAddr (organization)));

    maybe_add_string (ret, organization_notes_string,
                      gncOrganizationGetNotes (organization));

    currency = gncOrganizationGetCurrency (organization);
    if (currency)
        xmlAddChild (ret, commodity_ref_to_dom_tree (organization_currency_string,
                                                     currency));

    xmlAddChild (ret, int_to_dom_tree (organization_active_string,
                                       gncOrganizationGetActive (organization)));

    add_organization_entities (ret, organization);

    /* xmlAddChild won't do anything with a NULL, so tests are superfluous. */
    xmlAddChild (ret, qof_instance_slots_to_dom_tree (organization_slots_string,
                                                      QOF_INSTANCE (organization)));
    return ret;
}

/***********************************************************************/

struct organization_pdata
{
    GncOrganization* organization;
    QofBook* book;
};

static gboolean
set_string (xmlNodePtr node, GncOrganization* organization,
            void (*func) (GncOrganization* organization, const char* txt))
{
    char* txt = dom_tree_to_text (node);
    g_return_val_if_fail (txt, FALSE);

    func (organization, txt);

    g_free (txt);

    return TRUE;
}

static gboolean
set_boolean (xmlNodePtr node, GncOrganization* organization,
             void (*func) (GncOrganization* organization, gboolean b))
{
    gint64 val;
    gboolean ret;

    ret = dom_tree_to_integer (node, &val);
    if (ret)
        func (organization, (gboolean)val);

    return ret;
}

static gboolean
organization_name_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);

    return set_string (node, pdata->organization, gncOrganizationSetName);
}

static gboolean
organization_guid_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);
    GncGUID* guid;
    GncOrganization* organization;

    guid = dom_tree_to_guid (node);
    g_return_val_if_fail (guid, FALSE);
    organization = gncOrganizationLookup (pdata->book, guid);
    if (organization)
    {
        gncOrganizationDestroy (pdata->organization);
        pdata->organization = organization;
        gncOrganizationBeginEdit (organization);
    }
    else
    {
        gncOrganizationSetGUID (pdata->organization, guid);
    }

    guid_free (guid);

    return TRUE;
}

static gboolean
organization_id_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);

    return set_string (node, pdata->organization, gncOrganizationSetID);
}

static gboolean
organization_notes_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);

    return set_string (node, pdata->organization, gncOrganizationSetNotes);
}

static gboolean
organization_addr_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);
    GncAddress* addr = gncOrganizationGetAddr (pdata->organization);

    /* Unlike GncVendor (whose address is created alongside the vendor
     * itself), GncOrganization has no address until one is explicitly set
     * -- create it here on first load so gnc_dom_tree_to_address() has
     * somewhere to write into. */
    if (!addr)
    {
        addr = gncAddressCreate (pdata->book, QOF_INSTANCE (pdata->organization));
        gncOrganizationSetAddr (pdata->organization, addr);
    }

    return gnc_dom_tree_to_address (node, addr);
}

static gboolean
organization_currency_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);
    gnc_commodity* com;

    com = dom_tree_to_commodity_ref (node, pdata->book);
    g_return_val_if_fail (com, FALSE);

    gncOrganizationSetCurrency (pdata->organization, com);

    return TRUE;
}

static gboolean
organization_active_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);
    return set_boolean (node, pdata->organization, gncOrganizationSetActive);
}

static gboolean
organization_entities_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);
    xmlNodePtr mark;

    g_return_val_if_fail (node, FALSE);

    /* Unlike trn:splits (which always has at least two children), an
     * organization legitimately has zero member entities -- an empty
     * wrapper node is valid and simply yields no AddEntity calls. */
    for (mark = node->xmlChildrenNode; mark; mark = mark->next)
    {
        xmlChar* qof_type;
        xmlNodePtr guid_node;
        GncGUID* guid;
        QofCollection* col;
        QofInstance* entity;

        if (g_strcmp0 ("text", (char*)mark->name) == 0)
            continue;

        if (g_strcmp0 (organization_entity_string, (char*)mark->name))
            return FALSE;

        qof_type = xmlGetProp (mark, BAD_CAST organization_entity_qof_type_string);
        if (!qof_type)
            return FALSE;

        guid_node = mark->xmlChildrenNode;
        while (guid_node && g_strcmp0 ("text", (char*)guid_node->name) == 0)
            guid_node = guid_node->next;

        if (!guid_node ||
            g_strcmp0 (organization_entity_guid_string, (char*)guid_node->name))
        {
            xmlFree (qof_type);
            return FALSE;
        }

        guid = dom_tree_to_guid (guid_node);
        if (!guid)
        {
            xmlFree (qof_type);
            return FALSE;
        }

        /* The referenced entity must already exist in the book at this
         * point: accounts (the only entity type any current producer of
         * this format links -- see gnc-fincosys-sync.cpp) are always
         * written before business objects in the XML file (see
         * write_book() in io-gncxml-v2.cpp), and the sixtp parser
         * processes elements in file order, so by the time this handler
         * runs the entity has already been created. A reference that
         * still can't be resolved (e.g. a hand-edited or corrupt file) is
         * logged and skipped rather than aborting the whole organization
         * parse. */
        col = qof_book_get_collection (pdata->book, (const char*) qof_type);
        entity = col ? qof_collection_lookup_entity (col, guid) : NULL;
        if (entity)
            gncOrganizationAddEntity (pdata->organization, entity);
        else
            PWARN ("organization entity reference not found: type=%s",
                  (const char*) qof_type);

        guid_free (guid);
        xmlFree (qof_type);
    }
    return TRUE;
}

static gboolean
organization_slots_handler (xmlNodePtr node, gpointer organization_pdata)
{
    struct organization_pdata* pdata = static_cast<decltype (pdata)> (organization_pdata);
    return dom_tree_create_instance_slots (node, QOF_INSTANCE (pdata->organization));

}

static struct dom_tree_handler organization_handlers_v2[] =
{
    { organization_name_string, organization_name_handler, 1, 0 },
    { organization_guid_string, organization_guid_handler, 1, 0 },
    { organization_id_string, organization_id_handler, 1, 0 },
    { organization_addr_string, organization_addr_handler, 0, 0 },
    { organization_notes_string, organization_notes_handler, 0, 0 },
    { organization_currency_string, organization_currency_handler, 0, 0 },
    { organization_active_string, organization_active_handler, 1, 0 },
    { organization_entities_string, organization_entities_handler, 0, 0 },
    { organization_slots_string, organization_slots_handler, 0, 0 },
    { NULL, 0, 0, 0 }
};

static GncOrganization*
dom_tree_to_organization (xmlNodePtr node, QofBook* book)
{
    struct organization_pdata organization_pdata;
    gboolean successful;

    organization_pdata.organization = gncOrganizationCreate (book);
    organization_pdata.book = book;
    gncOrganizationBeginEdit (organization_pdata.organization);

    successful = dom_tree_generic_parse (node, organization_handlers_v2,
                                         &organization_pdata);

    if (successful)
        gncOrganizationCommitEdit (organization_pdata.organization);
    else
    {
        PERR ("failed to parse organization tree");
        gncOrganizationDestroy (organization_pdata.organization);
        organization_pdata.organization = NULL;
    }

    return organization_pdata.organization;
}

static gboolean
gnc_organization_end_handler (gpointer data_for_children,
                              GSList* data_from_children, GSList* sibling_data,
                              gpointer parent_data, gpointer global_data,
                              gpointer* result, const gchar* tag)
{
    GncOrganization* organization;
    xmlNodePtr tree = (xmlNodePtr)data_for_children;
    gxpf_data* gdata = (gxpf_data*)global_data;
    QofBook* book = static_cast<decltype (book)> (gdata->bookdata);

    if (parent_data)
    {
        return TRUE;
    }

    /* OK.  For some messed up reason this is getting called again with a
       NULL tag.  So we ignore those cases */
    if (!tag)
    {
        return TRUE;
    }

    g_return_val_if_fail (tree, FALSE);

    organization = dom_tree_to_organization (tree, book);
    if (organization != NULL)
    {
        gdata->cb (tag, gdata->parsedata, organization);
    }

    xmlFreeNode (tree);

    return organization != NULL;
}

static sixtp*
organization_sixtp_parser_create (void)
{
    return sixtp_dom_parser_new (gnc_organization_end_handler, NULL, NULL);
}

static gboolean
organization_should_be_saved (GncOrganization* organization)
{
    const char* id;

    /* make sure this is a valid organization before we save it -- should
     * have an ID (matches gnc_organizations_to_fincosys_json()'s own
     * skip-empty-code rule in gnc-fincosys-sync.cpp) */
    id = gncOrganizationGetID (organization);
    if (id == NULL || *id == '\0')
        return FALSE;

    return TRUE;
}

static void
do_count (QofInstance* organization_p, gpointer count_p)
{
    int* count = static_cast<decltype (count)> (count_p);
    if (organization_should_be_saved ((GncOrganization*)organization_p))
        (*count)++;
}

static int
organization_get_count (QofBook* book)
{
    int count = 0;
    qof_object_foreach (_GNC_MOD_NAME, book, do_count, (gpointer) &count);
    return count;
}

static void
xml_add_organization (QofInstance* organization_p, gpointer out_p)
{
    xmlNodePtr node;
    GncOrganization* organization = (GncOrganization*) organization_p;
    FILE* out = static_cast<decltype (out)> (out_p);

    if (ferror (out))
        return;
    if (!organization_should_be_saved (organization))
        return;

    node = organization_dom_tree_create (organization);
    xmlElemDump (out, NULL, node);
    xmlFreeNode (node);
    if (ferror (out) || fprintf (out, "\n") < 0)
        return;
}

static gboolean
organization_write (FILE* out, QofBook* book)
{
    qof_object_foreach_sorted (_GNC_MOD_NAME, book, xml_add_organization,
                               (gpointer) out);
    return ferror (out) == 0;
}

static gboolean
organization_ns (FILE* out)
{
    g_return_val_if_fail (out, FALSE);
    return gnc_xml2_write_namespace_decl (out, "organization");
}

void
gnc_organization_xml_initialize (void)
{
    static GncXmlDataType_t be_data =
    {
        GNC_FILE_BACKEND_VERS,
        gnc_organization_string,
        organization_sixtp_parser_create,
        NULL,           /* add_item */
        organization_get_count,
        organization_write,
        NULL,           /* scrub */
        organization_ns,
    };

    gnc_xml_register_backend(be_data);
}
