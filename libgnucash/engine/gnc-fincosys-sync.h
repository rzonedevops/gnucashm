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
 *  Each organization's \c "evidence_refs" / \c "legal_categories" arrays
 *  (the revstream1/ad-res-j7 case-evidence provenance fields -- see
 *  fincosys-atomspace-builder's \c CaseEvidenceEnricher) are recovered
 *  from tagged lines in the organization's \c notes field, as previously
 *  written there by gnc_organizations_from_fincosys_json() -- see that
 *  function's doc comment for why \c notes is used rather than a
 *  dedicated KVP slot.
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
 *  Each organization's \c "evidence_refs" / \c "legal_categories" string
 *  arrays, if present, are recorded on the created GncOrganization's
 *  \c notes field as tagged comma-separated lines (GncOrganization has no
 *  generic KVP accessor of its own), so a later
 *  gnc_organizations_to_fincosys_json() call can round-trip them back out.
 *
 *  @param book The QofBook to create organizations/accounts in.
 *  @param json The JSON document text.
 *  @return The number of organizations imported, or -1 if @a json could
 *          not be parsed.
 */
gint gnc_organizations_from_fincosys_json (QofBook *book, const gchar *json);

/** Parse a JSON document matching the "GnuCash sync-feed" schema (\c
 *  "schema_version": "1.0") produced by fincosys-atomspace-builder's
 *  \c GnuCashSyncExporter (see its
 *  \c atomspace_builder/exporters/gnucash_exporter.py) and materialize
 *  full double-entry Account and Transaction/Split records in @a book.
 *
 *  Unlike gnc_organizations_from_fincosys_json(), which only carries
 *  organization/account metadata, this schema also carries transactions
 *  with balanced splits -- it is the counterpart that lets gnucashm
 *  round-trip actual ledger activity synced from fincosys, including the
 *  synthetic "Imbalance-*" counter-accounts the exporter generates for
 *  fincosys's single-sided bank-statement source data.
 *
 *  All accounts in the document's "accounts" array are created (and
 *  linked into the account tree via each entry's "parent_code", falling
 *  back to the book's root account when absent or unresolved) before any
 *  transaction is processed, so a transaction's splits may reference an
 *  account appearing anywhere in the array regardless of order. A split
 *  whose "account_code" has no matching account is skipped (logged)
 *  rather than aborting the whole transaction -- a partial/unbalanced
 *  import is still useful for manual reconciliation, mirroring how
 *  gnc_organizations_from_fincosys_json() degrades gracefully on missing
 *  identifiers.
 *
 *  @param book The QofBook to create accounts/transactions in.
 *  @param json The JSON document text.
 *  @return The number of transactions imported, or -1 if @a json could
 *          not be parsed.
 */
gint gnc_transactions_from_syncfeed_json (QofBook *book, const gchar *json);

#ifdef __cplusplus
}
#endif

#endif /* GNC_FINCOSYS_SYNC_H */
/** @} */
