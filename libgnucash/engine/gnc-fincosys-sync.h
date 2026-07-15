/********************************************************************\
 * gnc-fincosys-sync.h -- Fincosys ecosystem sync bridge             *
 * Copyright 2026 GnuCash Contributors                                *
 *                                                                    *
 * This program is free software; you can redistribute it and/or      *
 * modify it under the terms of the GNU General Public License as     *
 * published by the Free Software Foundation; either version 2 of     *
 * the License, or (at your option) any later version.                *
 *                                                                    *
 * This program is distributed in the hope that it will be useful,    *
 * but WITHOUT ANY WARRANTY; without even the implied warranty of     *
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the      *
 * GNU General Public License for more details.                       *
 *                                                                    *
 * You should have received a copy of the GNU General Public License*
 * along with this program; if not, contact:                        *
 *                                                                  *
 * Free Software Foundation           Voice:  +1-617-542-5942       *
 * 51 Franklin Street, Fifth Floor    Fax:    +1-617-542-2652       *
 * Boston, MA  02110-1301,  USA       gnu@gnu.org                   *
 *                                                                  *
\********************************************************************/
/** @addtogroup Engine
    @{ */
/** @file gnc-fincosys-sync.h
    @brief Serialize/parse gnucashm multi-entity organizations to/from the
           shared "fincosys-ecosystem-sync/v1" schema.

    This is the integration point used by the external
    ``fincosys-atomspace-builder`` repository (see its
    ``atomspace_builder/loaders/gnucashm.py``) to sync gnucashm's
    QofMultiEntityCollection / GncOrganization records with fincosys and
    the wider GnuCash ecosystem (gnucashcog-v3, helix). See
    ORGANIZATION_ENHANCEMENTS.md for background on the multi-entity
    aggregation feature this bridges.
*/

#ifndef GNC_FINCOSYS_SYNC_H
#define GNC_FINCOSYS_SYNC_H

#include <glib.h>
#include "gncOrganization.h"
#include "qofbook.h"
#include "qofid.h"

#ifdef __cplusplus
extern "C"
{
#endif

/** Serialize a list of organizations (and each organization's Account
 *  entities) to the shared Fincosys Ecosystem Sync Schema v1
 *  (\c "schema": "fincosys-ecosystem-sync/v1", \c "source": "gnucashm").
 *
 *  @param organizations A GList of GncOrganization* to export.
 *  @return Newly allocated JSON string (caller frees with g_free()), or
 *          NULL if @a organizations is NULL.
 */
gchar *gnc_organizations_to_fincosys_json (GList *organizations);

/** Parse a JSON document matching the shared Fincosys Ecosystem Sync
 *  Schema v1 (as produced by fincosys-atomspace-builder's
 *  EcosystemSyncExporter, or by fincosys itself) and create a
 *  GncOrganization plus placeholder Account entries in @a book for each
 *  organization in the document.
 *
 *  This is a purpose-built parser scoped to the sync schema's shape
 *  (flat objects/arrays of strings/numbers/booleans/null) -- it is not a
 *  general-purpose JSON parser.
 *
 *  @param book The QofBook to create organizations/accounts in.
 *  @param json The JSON document text.
 *  @return The number of organizations imported, or -1 if @a json could
 *          not be parsed.
 */
gint gnc_organizations_from_fincosys_json (QofBook *book, const gchar *json);

#ifdef __cplusplus
}
#endif

#endif /* GNC_FINCOSYS_SYNC_H */
/** @} */
