/********************************************************************\
 * gncOrganization.c -- the Core Organization Interface             *
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
/*
 * Copyright (C) 2024 GnuCash Contributors
 * Author: GnuCash Contributors
 */

#ifdef HAVE_CONFIG_H
#include <config.h>
#endif
#include <glib.h>
#include <glib/gi18n.h>
#include <string.h>

#include "gncOrganization.h"
#include "gncOrganizationP.h"
#include "gncAddress.h"
#include "gncAddressP.h"
#include "gnc-commodity.h"
#include "qofinstance-p.h"
#include "qofclass.h"
#include "qof-string-cache.h"

#ifndef CACHE_INSERT
#define CACHE_INSERT(str) (str ? g_strdup(str) : g_strdup(""))
#endif

#ifndef CACHE_REMOVE
#define CACHE_REMOVE(str) do { if (str) { g_free((char*)str); str = NULL; } } while(0)
#endif

#ifndef safe_strcmp
#define safe_strcmp(a, b) g_strcmp0(a, b)
#endif

static QofLogModule log_module = GNC_MOD_BUSINESS;

/* Forward declaration */
static void mark_organization(GncOrganization *organization);

G_DEFINE_TYPE(GncOrganization, gnc_organization, QOF_TYPE_INSTANCE)

static void
gnc_organization_init(GncOrganization* org)
{
    org->id = CACHE_INSERT("");
    org->name = CACHE_INSERT("");
    org->notes = CACHE_INSERT("");
    org->addr = NULL;
    org->currency = NULL;
    org->active = TRUE;
    org->entities = NULL;
}

static void
gnc_organization_dispose(GObject* organizationp)
{
    G_OBJECT_CLASS(gnc_organization_parent_class)->dispose(organizationp);
}

static void
gnc_organization_finalize(GObject* organizationp)
{
    GncOrganization* org = GNC_ORGANIZATION(organizationp);

    CACHE_REMOVE(org->id);
    CACHE_REMOVE(org->name);
    CACHE_REMOVE(org->notes);

    if (org->addr)
        gncAddressDestroy(org->addr);

    g_list_free(org->entities);

    G_OBJECT_CLASS(gnc_organization_parent_class)->finalize(organizationp);
}

static void
gnc_organization_get_property(GObject* object,
                              guint prop_id,
                              GValue* value,
                              GParamSpec* pspec)
{
    GncOrganization* org = GNC_ORGANIZATION(object);

    switch (prop_id)
    {
    case PROP_ID:
        g_value_set_string(value, org->id);
        break;
    case PROP_NAME:
        g_value_set_string(value, org->name);
        break;
    case PROP_NOTES:
        g_value_set_string(value, org->notes);
        break;
    case PROP_ACTIVE:
        g_value_set_boolean(value, org->active);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
}

static void
gnc_organization_set_property(GObject* object,
                              guint prop_id,
                              const GValue* value,
                              GParamSpec* pspec)
{
    GncOrganization* org = GNC_ORGANIZATION(object);

    switch (prop_id)
    {
    case PROP_ID:
        gncOrganizationSetID(org, g_value_get_string(value));
        break;
    case PROP_NAME:
        gncOrganizationSetName(org, g_value_get_string(value));
        break;
    case PROP_NOTES:
        gncOrganizationSetNotes(org, g_value_get_string(value));
        break;
    case PROP_ACTIVE:
        gncOrganizationSetActive(org, g_value_get_boolean(value));
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
}

static void
gnc_organization_class_init(GncOrganizationClass* klass)
{
    GObjectClass* gobject_class = G_OBJECT_CLASS(klass);

    gobject_class->dispose = gnc_organization_dispose;
    gobject_class->finalize = gnc_organization_finalize;
    gobject_class->set_property = gnc_organization_set_property;
    gobject_class->get_property = gnc_organization_get_property;

    g_object_class_install_property(
        gobject_class,
        PROP_ID,
        g_param_spec_string(ORGANIZATION_ID,
                           "Organization ID",
                           "The organization ID is an arbitrary string",
                           NULL,
                           G_PARAM_READWRITE));

    g_object_class_install_property(
        gobject_class,
        PROP_NAME,
        g_param_spec_string(ORGANIZATION_NAME,
                           "Organization Name",
                           "The organization name is the name of the organization",
                           NULL,
                           G_PARAM_READWRITE));

    g_object_class_install_property(
        gobject_class,
        PROP_NOTES,
        g_param_spec_string(ORGANIZATION_NOTES,
                           "Organization Notes",
                           "The organization notes is an arbitrary string",
                           NULL,
                           G_PARAM_READWRITE));

    g_object_class_install_property(
        gobject_class,
        PROP_ACTIVE,
        g_param_spec_boolean(ORGANIZATION_ACTIVE,
                            "Active",
                            "TRUE if the organization is active",
                            TRUE,
                            G_PARAM_READWRITE));
}

/* Create/Destroy Functions */

GncOrganization *
gncOrganizationCreate(QofBook* book)
{
    GncOrganization* org;

    if (!book) return NULL;

    org = g_object_new(GNC_TYPE_ORGANIZATION, NULL);
    qof_instance_init_data(&org->inst, GNC_ID_ORGANIZATION, book);

    qof_event_gen(&org->inst, QOF_EVENT_CREATE, NULL);

    return org;
}

void
gncOrganizationDestroy(GncOrganization* org)
{
    if (!org) return;
    qof_instance_set_destroying(org, TRUE);
    gncOrganizationCommitEdit(org);
}

static void
org_free(QofInstance* inst)
{
    GncOrganization* org = GNC_ORGANIZATION(inst);
    gncOrganizationFree(org);
}

void
gncOrganizationFree(GncOrganization* org)
{
    if (!org) return;

    qof_event_gen(&org->inst, QOF_EVENT_DESTROY, NULL);
    g_object_unref(org);
}

/* Set Functions */

#define SET_STR(obj, member, str) { \
        char * tmp; \
        \
        if (!safe_strcmp (member, str)) return; \
        gncOrganizationBeginEdit (obj); \
        tmp = CACHE_INSERT (str); \
        CACHE_REMOVE (member); \
        member = tmp; \
        }

void gncOrganizationSetID(GncOrganization* org, const char* id)
{
    if (!org) return;
    SET_STR(org, org->id, id);
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

void gncOrganizationSetName(GncOrganization* org, const char* name)
{
    if (!org) return;
    SET_STR(org, org->name, name);
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

void gncOrganizationSetNotes(GncOrganization* org, const char* notes)
{
    if (!org) return;
    SET_STR(org, org->notes, notes);
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

void gncOrganizationSetCurrency(GncOrganization* org, gnc_commodity* currency)
{
    if (!org) return;
    if (org->currency == currency) return;
    gncOrganizationBeginEdit(org);
    org->currency = currency;
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

void gncOrganizationSetActive(GncOrganization* org, gboolean active)
{
    if (!org) return;
    if (org->active == active) return;
    gncOrganizationBeginEdit(org);
    org->active = active;
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

void gncOrganizationSetAddr(GncOrganization* org, GncAddress* addr)
{
    if (!org) return;
    if (org->addr == addr) return;
    gncOrganizationBeginEdit(org);
    if (org->addr)
        gncAddressDestroy(org->addr);
    org->addr = addr;
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

/* Get Functions */

const char* gncOrganizationGetID(const GncOrganization* org)
{
    if (!org) return NULL;
    return org->id;
}

const char* gncOrganizationGetName(const GncOrganization* org)
{
    if (!org) return NULL;
    return org->name;
}

const char* gncOrganizationGetNotes(const GncOrganization* org)
{
    if (!org) return NULL;
    return org->notes;
}

gnc_commodity* gncOrganizationGetCurrency(const GncOrganization* org)
{
    if (!org) return NULL;
    return org->currency;
}

gboolean gncOrganizationGetActive(const GncOrganization* org)
{
    if (!org) return FALSE;
    return org->active;
}

GncAddress* gncOrganizationGetAddr(const GncOrganization* org)
{
    if (!org) return NULL;
    return org->addr;
}

/* Multi-Entity Management Functions */

void gncOrganizationAddEntity(GncOrganization* org, QofInstance* entity)
{
    if (!org || !entity) return;
    
    /* Check if entity is already in the list */
    if (g_list_find(org->entities, entity)) return;
    
    gncOrganizationBeginEdit(org);
    org->entities = g_list_prepend(org->entities, entity);
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

void gncOrganizationRemoveEntity(GncOrganization* org, QofInstance* entity)
{
    if (!org || !entity) return;
    
    gncOrganizationBeginEdit(org);
    org->entities = g_list_remove(org->entities, entity);
    mark_organization(org);
    gncOrganizationCommitEdit(org);
}

GList* gncOrganizationGetEntities(const GncOrganization* org)
{
    if (!org) return NULL;
    return org->entities;
}

guint gncOrganizationGetEntityCount(const GncOrganization* org)
{
    if (!org) return 0;
    return g_list_length(org->entities);
}

/* Comparison function */

int gncOrganizationCompare(const GncOrganization* a, const GncOrganization* b)
{
    if (!a && !b) return 0;
    if (!a) return -1;
    if (!b) return 1;

    return g_strcmp0(a->name, b->name);
}

/* ============================================================== */
/* misc inline funcs */

static void mark_organization (GncOrganization *organization)
{
    qof_instance_set_dirty(&organization->inst);
    qof_event_gen (&organization->inst, QOF_EVENT_MODIFY, NULL);
}

void gncOrganizationBeginEdit (GncOrganization *organization)
{
    qof_begin_edit(QOF_INSTANCE(organization));
}

static void gncOrganizationOnError (QofInstance *inst, QofBackendError errcode)
{
    PERR("Organization QofBackend Failure: %d", errcode);
    gnc_engine_signal_commit_error( errcode );
}

static void gncOrganizationOnDone (QofInstance *inst)
{
    GncOrganization *organization = (GncOrganization *) inst;
    gncAddressClearDirty (organization->addr);
}

void gncOrganizationCommitEdit (GncOrganization *organization)
{
    if (!qof_commit_edit (QOF_INSTANCE(organization))) return;
    qof_commit_edit_part2 (QOF_INSTANCE(organization), gncOrganizationOnError,
                          gncOrganizationOnDone, org_free);
}

/* ============================================================== */

/* QOF Registration */

static QofObject organization_object_def =
{
    DI(.interface_version = ) QOF_OBJECT_VERSION,
    DI(.e_type            = ) GNC_ID_ORGANIZATION,
    DI(.type_label        = ) "Organization",
    DI(.create            = ) (gpointer)gncOrganizationCreate,
    DI(.book_begin        = ) NULL,
    DI(.book_end          = ) NULL,
    DI(.is_dirty          = ) qof_collection_is_dirty,
    DI(.mark_clean        = ) qof_collection_mark_clean,
    DI(.foreach           = ) qof_collection_foreach,
    DI(.printable         = ) (const char* (*)(gpointer)) gncOrganizationGetName,
    DI(.version_cmp       = ) (int (*)(gpointer, gpointer)) qof_instance_version_cmp,
};

gboolean gncOrganizationRegister(void)
{
    static QofParam params[] =
    {
        { ORGANIZATION_ID, QOF_TYPE_STRING, (QofAccessFunc)gncOrganizationGetID, (QofSetterFunc)gncOrganizationSetID },
        { ORGANIZATION_NAME, QOF_TYPE_STRING, (QofAccessFunc)gncOrganizationGetName, (QofSetterFunc)gncOrganizationSetName },
        { ORGANIZATION_NOTES, QOF_TYPE_STRING, (QofAccessFunc)gncOrganizationGetNotes, (QofSetterFunc)gncOrganizationSetNotes },
        { ORGANIZATION_CURRENCY, GNC_ID_COMMODITY, (QofAccessFunc)gncOrganizationGetCurrency, (QofSetterFunc)gncOrganizationSetCurrency },
        { ORGANIZATION_ACTIVE, QOF_TYPE_BOOLEAN, (QofAccessFunc)gncOrganizationGetActive, (QofSetterFunc)gncOrganizationSetActive },
        { ORGANIZATION_ADDR, GNC_ID_ADDRESS, (QofAccessFunc)gncOrganizationGetAddr, (QofSetterFunc)gncOrganizationSetAddr },
        { QOF_PARAM_BOOK, QOF_ID_BOOK, (QofAccessFunc)qof_instance_get_book, NULL },
        { QOF_PARAM_GUID, QOF_TYPE_GUID, (QofAccessFunc)qof_instance_get_guid, NULL },
        { NULL },
    };

    qof_class_register(GNC_ID_ORGANIZATION, (QofSortFunc)gncOrganizationCompare, params);

    return qof_object_register(&organization_object_def);
}