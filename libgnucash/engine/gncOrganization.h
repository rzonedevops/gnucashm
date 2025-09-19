/********************************************************************\
 * gncOrganization.h -- the Core Organization Interface             *
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
/** @addtogroup Business
    @{ */
/** @addtogroup Organization
    @{ */
/** @file gncOrganization.h
    @brief Core Organization Interface
    @author Copyright (C) 2024 GnuCash Contributors
*/

#ifndef GNC_ORGANIZATION_H_
#define GNC_ORGANIZATION_H_

/** @struct GncOrganization

Organizations represent parent entities that can contain multiple
business entities like customers, vendors, and employees.

@param	QofInstance     inst;
@param	char *          id;
@param	char *          name;
@param	char *          notes;
@param	GncAddress *    addr;
@param	gnc_commodity * currency;
@param	gboolean        active;
@param	GList *         entities;
*/

#include "gncAddress.h"
#include "gncBillTerm.h"
#include "gncTaxTable.h"
#include "gnc-commodity.h"
#include "qof.h"

#define GNC_ID_ORGANIZATION    "gncOrganization"
#define GNC_TYPE_ORGANIZATION  (gnc_organization_get_type ())
G_DECLARE_FINAL_TYPE (GncOrganization, gnc_organization, GNC, ORGANIZATION, QofInstance)

#ifdef __cplusplus
extern "C" {
#endif

/** @name Create/Destroy Functions
 @{ */
GncOrganization *gncOrganizationCreate (QofBook *book);
void gncOrganizationDestroy (GncOrganization *org);
/** @} */

/** @name Set Functions
 @{ */
void gncOrganizationSetID (GncOrganization *org, const char *id);
void gncOrganizationSetName (GncOrganization *org, const char *name);
void gncOrganizationSetNotes (GncOrganization *org, const char *notes);
void gncOrganizationSetCurrency (GncOrganization *org, gnc_commodity *currency);
void gncOrganizationSetActive (GncOrganization *org, gboolean active);

void gncOrganizationSetAddr (GncOrganization *org, GncAddress *addr);
/** @} */

/** @name Get Functions
 @{ */
const char * gncOrganizationGetID (const GncOrganization *org);
const char * gncOrganizationGetName (const GncOrganization *org);
const char * gncOrganizationGetNotes (const GncOrganization *org);
gnc_commodity * gncOrganizationGetCurrency (const GncOrganization *org);
gboolean gncOrganizationGetActive (const GncOrganization *org);

GncAddress * gncOrganizationGetAddr (const GncOrganization *org);
/** @} */

/** @name Multi-Entity Management Functions
 @{ */
void gncOrganizationAddEntity (GncOrganization *org, QofInstance *entity);
void gncOrganizationRemoveEntity (GncOrganization *org, QofInstance *entity);
GList * gncOrganizationGetEntities (const GncOrganization *org);
guint gncOrganizationGetEntityCount (const GncOrganization *org);
/** @} */

/** @name QOF Functions
 @{ */
gboolean gncOrganizationRegister (void);
/** @} */

/** Compare Organizations */
int gncOrganizationCompare (const GncOrganization *a, const GncOrganization *b);

#define ORGANIZATION_ID         "id"
#define ORGANIZATION_NAME       "name"  
#define ORGANIZATION_NOTES      "notes"
#define ORGANIZATION_CURRENCY   "currency"
#define ORGANIZATION_ACTIVE     "active"
#define ORGANIZATION_ADDR       "addr"

#ifdef __cplusplus
}
#endif

#endif /* GNC_ORGANIZATION_H_ */
/** @} */
/** @} */