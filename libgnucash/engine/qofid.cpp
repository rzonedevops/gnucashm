/********************************************************************\
 * qofid.c -- QOF entity identifier implementation                  *
 * Copyright (C) 2000 Dave Peticolas <dave@krondo.com>              *
 * Copyright (C) 2003 Linas Vepstas <linas@linas.org>               *
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

#include <glib.h>

#include <config.h>
#include <string.h>

#include "qof.h"
#include "qofid-p.h"
#include "qofinstance-p.h"

static QofLogModule log_module = QOF_MOD_ENGINE;

struct QofCollection_s
{
    QofIdType    e_type;
    gboolean     is_dirty;

    GHashTable * hash_of_entities;
    gpointer     data;       /* place where object class can hang arbitrary data */
};

/* =============================================================== */

QofCollection *
qof_collection_new (QofIdType type)
{
    QofCollection *col;
    col = g_new0(QofCollection, 1);
    col->e_type = static_cast<QofIdType>(CACHE_INSERT (type));
    col->hash_of_entities = guid_hash_table_new();
    col->data = NULL;
    return col;
}

void
qof_collection_destroy (QofCollection *col)
{
    CACHE_REMOVE (col->e_type);
    g_hash_table_destroy(col->hash_of_entities);
    col->e_type = NULL;
    col->hash_of_entities = NULL;
    col->data = NULL;   /** XXX there should be a destroy notifier for this */
    g_free (col);
}

/* =============================================================== */
/* getters */

QofIdType
qof_collection_get_type (const QofCollection *col)
{
    return col->e_type;
}

/* =============================================================== */

void
qof_collection_remove_entity (QofInstance *ent)
{
    QofCollection *col;
    const GncGUID *guid;

    if (!ent) return;
    col = qof_instance_get_collection(ent);
    if (!col) return;
    guid = qof_instance_get_guid(ent);
    g_hash_table_remove (col->hash_of_entities, guid);
    qof_instance_set_collection(ent, NULL);
}

void
qof_collection_insert_entity (QofCollection *col, QofInstance *ent)
{
    const GncGUID *guid;

    if (!col || !ent) return;
    guid = qof_instance_get_guid(ent);
    if (guid_equal(guid, guid_null())) return;
    g_return_if_fail (col->e_type == ent->e_type);
    qof_collection_remove_entity (ent);
    g_hash_table_insert (col->hash_of_entities, (gpointer)guid, ent);
    qof_instance_set_collection(ent, col);
}

gboolean
qof_collection_add_entity (QofCollection *coll, QofInstance *ent)
{
    QofInstance *e;
    const GncGUID *guid;

    e = NULL;
    if (!coll || !ent)
    {
        return FALSE;
    }
    guid = qof_instance_get_guid(ent);
    if (guid_equal(guid, guid_null()))
    {
        return FALSE;
    }
    g_return_val_if_fail (coll->e_type == ent->e_type, FALSE);
    e = qof_collection_lookup_entity(coll, guid);
    if ( e != NULL )
    {
        return FALSE;
    }
    g_hash_table_insert (coll->hash_of_entities, (gpointer)guid, ent);
    return TRUE;
}


static void
collection_compare_cb (QofInstance *ent, gpointer user_data)
{
    QofCollection *target;
    QofInstance *e;
    const GncGUID *guid;
    gint value;

    e = NULL;
    target = (QofCollection*)user_data;
    if (!target || !ent)
    {
        return;
    }
    value = *(gint*)qof_collection_get_data(target);
    if (value != 0)
    {
        return;
    }
    guid = qof_instance_get_guid(ent);
    if (guid_equal(guid, guid_null()))
    {
        value = -1;
        qof_collection_set_data(target, &value);
        return;
    }
    g_return_if_fail (target->e_type == ent->e_type);
    e = qof_collection_lookup_entity(target, guid);
    if ( e == NULL )
    {
        value = 1;
        qof_collection_set_data(target, &value);
        return;
    }
    value = 0;
    qof_collection_set_data(target, &value);
}

gint
qof_collection_compare (QofCollection *target, QofCollection *merge)
{
    gint value;

    value = 0;
    if (!target && !merge)
    {
        return 0;
    }
    if (target == merge)
    {
        return 0;
    }
    if (!target && merge)
    {
        return -1;
    }
    if (target && !merge)
    {
        return 1;
    }
    if (target->e_type != merge->e_type)
    {
        return -1;
    }
    qof_collection_set_data(target, &value);
    qof_collection_foreach(merge, collection_compare_cb, target);
    value = *(gint*)qof_collection_get_data(target);
    if (value == 0)
    {
        qof_collection_set_data(merge, &value);
        qof_collection_foreach(target, collection_compare_cb, merge);
        value = *(gint*)qof_collection_get_data(merge);
    }
    return value;
}

QofInstance *
qof_collection_lookup_entity (const QofCollection *col, const GncGUID * guid)
{
    QofInstance *ent;
    g_return_val_if_fail (col, NULL);
    if (guid == NULL) return NULL;
    ent = static_cast<QofInstance*>(g_hash_table_lookup (col->hash_of_entities,
							 guid));
    if (ent != NULL && qof_instance_get_destroying(ent)) return NULL;	
    return ent;
}

QofCollection *
qof_collection_from_glist (QofIdType type, const GList *glist)
{
    QofCollection *coll;
    QofInstance *ent;
    const GList *list;

    coll = qof_collection_new(type);
    for (list = glist; list != NULL; list = list->next)
    {
        ent = QOF_INSTANCE(list->data);
        if (FALSE == qof_collection_add_entity(coll, ent))
        {
            qof_collection_destroy(coll);
            return NULL;
        }
    }
    return coll;
}

guint
qof_collection_count (const QofCollection *col)
{
    guint c;

    c = g_hash_table_size(col->hash_of_entities);
    return c;
}

/* =============================================================== */

gboolean
qof_collection_is_dirty (const QofCollection *col)
{
    return col ? col->is_dirty : FALSE;
}

void
qof_collection_mark_clean (QofCollection *col)
{
    if (col)
    {
        col->is_dirty = FALSE;
    }
}

void
qof_collection_mark_dirty (QofCollection *col)
{
    if (col)
    {
        col->is_dirty = TRUE;
    }
}

void
qof_collection_print_dirty (const QofCollection *col, gpointer dummy)
{
    if (col->is_dirty)
        printf("%s collection is dirty.\n", col->e_type);
    qof_collection_foreach(col, (QofInstanceForeachCB)qof_instance_print_dirty, NULL);
}

/* =============================================================== */

gpointer
qof_collection_get_data (const QofCollection *col)
{
    return col ? col->data : NULL;
}

void
qof_collection_set_data (QofCollection *col, gpointer user_data)
{
    if (col)
    {
        col->data = user_data;
    }
}

/* =============================================================== */

void
qof_collection_foreach_sorted (const QofCollection *col, QofInstanceForeachCB cb_func,
                               gpointer user_data, GCompareFunc sort_fn)
{
    GList *entries;

    g_return_if_fail (col);
    g_return_if_fail (cb_func);

    PINFO("Hash Table size of %s before is %d", col->e_type, g_hash_table_size(col->hash_of_entities));

    entries = g_hash_table_get_values (col->hash_of_entities);
    if (sort_fn)
        entries = g_list_sort (entries, sort_fn);
    g_list_foreach (entries, (GFunc)cb_func, user_data);
    g_list_free (entries);

    PINFO("Hash Table size of %s after is %d", col->e_type, g_hash_table_size(col->hash_of_entities));
}

void
qof_collection_foreach (const QofCollection *col, QofInstanceForeachCB cb_func,
                        gpointer user_data)
{
    qof_collection_foreach_sorted (col, cb_func, user_data, nullptr);
}

/* =============================================================== */
/* Multi-Entity Collection Implementation */

struct QofMultiEntityCollection_s
{
    GHashTable * entity_table;     /* Hash table of entities (key: GUID, value: QofInstance) */
    GHashTable * type_table;       /* Hash table of types present (key: QofIdType, value: count) */
};

QofMultiEntityCollection *
qof_multi_entity_collection_new (void)
{
    QofMultiEntityCollection *multi_coll;
    
    multi_coll = g_new0 (QofMultiEntityCollection, 1);
    multi_coll->entity_table = g_hash_table_new (g_direct_hash, g_direct_equal);
    multi_coll->type_table = g_hash_table_new_full (g_str_hash, g_str_equal, NULL, NULL);
    
    return multi_coll;
}

void
qof_multi_entity_collection_destroy (QofMultiEntityCollection *multi_coll)
{
    if (!multi_coll) return;
    
    g_hash_table_destroy (multi_coll->entity_table);
    g_hash_table_destroy (multi_coll->type_table);
    g_free (multi_coll);
}

static void
add_entity_to_multi_collection_cb (QofInstance *entity, gpointer user_data)
{
    QofMultiEntityCollection *multi_coll = (QofMultiEntityCollection *)user_data;
    qof_multi_entity_collection_add_entity (multi_coll, entity);
}

void
qof_multi_entity_collection_add_collection (QofMultiEntityCollection *multi_coll,
                                             const QofCollection *coll)
{
    if (!multi_coll || !coll) return;
    
    qof_collection_foreach (coll, add_entity_to_multi_collection_cb, multi_coll);
}

struct FilterData
{
    QofMultiEntityCollection *multi_coll;
    QofEntityFilterCB filter;
    gpointer user_data;
};

static void
add_entity_filtered_cb (QofInstance *entity, gpointer user_data)
{
    struct FilterData *filter_data = (struct FilterData *)user_data;
    
    if (filter_data->filter (entity, filter_data->user_data))
    {
        qof_multi_entity_collection_add_entity (filter_data->multi_coll, entity);
    }
}

void
qof_multi_entity_collection_add_collection_filtered (QofMultiEntityCollection *multi_coll,
                                                      const QofCollection *coll,
                                                      QofEntityFilterCB filter,
                                                      gpointer user_data)
{
    struct FilterData filter_data;
    
    if (!multi_coll || !coll || !filter) return;
    
    filter_data.multi_coll = multi_coll;
    filter_data.filter = filter;
    filter_data.user_data = user_data;
    
    qof_collection_foreach (coll, add_entity_filtered_cb, &filter_data);
}

gboolean
qof_multi_entity_collection_add_entity (QofMultiEntityCollection *multi_coll,
                                         QofInstance *entity)
{
    const GncGUID *guid;
    const char *type;
    gpointer count_ptr;
    guint count;
    
    if (!multi_coll || !entity) return FALSE;
    
    guid = qof_instance_get_guid (entity);
    if (!guid || guid_equal (guid, guid_null ())) return FALSE;
    
    /* Check if entity is already present */
    if (g_hash_table_lookup (multi_coll->entity_table, guid))
        return FALSE;
    
    /* Add entity */
    g_hash_table_insert (multi_coll->entity_table, (gpointer)guid, entity);
    
    /* Update type count */
    type = entity->e_type;
    count_ptr = g_hash_table_lookup (multi_coll->type_table, type);
    count = GPOINTER_TO_UINT (count_ptr) + 1;
    g_hash_table_insert (multi_coll->type_table, (gpointer)type, GUINT_TO_POINTER (count));
    
    return TRUE;
}

gboolean
qof_multi_entity_collection_remove_entity (QofMultiEntityCollection *multi_coll,
                                            QofInstance *entity)
{
    const GncGUID *guid;
    const char *type;
    gpointer count_ptr;
    guint count;
    
    if (!multi_coll || !entity) return FALSE;
    
    guid = qof_instance_get_guid (entity);
    if (!guid) return FALSE;
    
    /* Check if entity is present */
    if (!g_hash_table_lookup (multi_coll->entity_table, guid))
        return FALSE;
    
    /* Remove entity */
    g_hash_table_remove (multi_coll->entity_table, guid);
    
    /* Update type count */
    type = entity->e_type;
    count_ptr = g_hash_table_lookup (multi_coll->type_table, type);
    count = GPOINTER_TO_UINT (count_ptr);
    if (count > 1)
    {
        g_hash_table_insert (multi_coll->type_table, (gpointer)type, GUINT_TO_POINTER (count - 1));
    }
    else
    {
        g_hash_table_remove (multi_coll->type_table, type);
    }
    
    return TRUE;
}

guint
qof_multi_entity_collection_count (const QofMultiEntityCollection *multi_coll)
{
    if (!multi_coll) return 0;
    return g_hash_table_size (multi_coll->entity_table);
}

gboolean
qof_multi_entity_collection_contains (const QofMultiEntityCollection *multi_coll,
                                       const QofInstance *entity)
{
    const GncGUID *guid;
    
    if (!multi_coll || !entity) return FALSE;
    
    guid = qof_instance_get_guid (entity);
    if (!guid) return FALSE;
    
    return g_hash_table_lookup (multi_coll->entity_table, guid) != NULL;
}

void
qof_multi_entity_collection_foreach (const QofMultiEntityCollection *multi_coll,
                                      QofInstanceForeachCB cb_func,
                                      gpointer user_data)
{
    qof_multi_entity_collection_foreach_sorted (multi_coll, cb_func, user_data, NULL);
}

void
qof_multi_entity_collection_foreach_sorted (const QofMultiEntityCollection *multi_coll,
                                             QofInstanceForeachCB cb_func,
                                             gpointer user_data,
                                             GCompareFunc sort_fn)
{
    GList *entities;
    
    if (!multi_coll || !cb_func) return;
    
    entities = g_hash_table_get_values (multi_coll->entity_table);
    if (sort_fn)
        entities = g_list_sort (entities, sort_fn);
    
    g_list_foreach (entities, (GFunc)cb_func, user_data);
    g_list_free (entities);
}

GList *
qof_multi_entity_collection_get_types (const QofMultiEntityCollection *multi_coll)
{
    if (!multi_coll) return NULL;
    return g_hash_table_get_keys (multi_coll->type_table);
}

QofMultiEntityCollection *
qof_multi_entity_collection_filter (const QofMultiEntityCollection *multi_coll,
                                     QofEntityFilterCB filter,
                                     gpointer user_data)
{
    struct FilterData filter_data;
    QofMultiEntityCollection *filtered_coll;
    
    if (!multi_coll || !filter) return NULL;
    
    filtered_coll = qof_multi_entity_collection_new ();
    filter_data.multi_coll = filtered_coll;
    filter_data.filter = filter;
    filter_data.user_data = user_data;
    
    qof_multi_entity_collection_foreach (multi_coll, add_entity_filtered_cb, &filter_data);
    
    return filtered_coll;
}

QofMultiEntityCollection *
qof_multi_entity_collection_merge (const QofMultiEntityCollection *coll1,
                                    const QofMultiEntityCollection *coll2)
{
    QofMultiEntityCollection *merged_coll;
    
    if (!coll1 && !coll2) return NULL;
    
    merged_coll = qof_multi_entity_collection_new ();
    
    if (coll1)
    {
        qof_multi_entity_collection_foreach (coll1, add_entity_to_multi_collection_cb, merged_coll);
    }
    
    if (coll2)
    {
        qof_multi_entity_collection_foreach (coll2, add_entity_to_multi_collection_cb, merged_coll);
    }
    
    return merged_coll;
}

/* =============================================================== */
