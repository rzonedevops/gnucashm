/********************************************************************\
 * gncOrganizationP.h -- the Core Organization Interface (Private)  *
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

#ifndef GNC_ORGANIZATIONP_H_
#define GNC_ORGANIZATIONP_H_

#include "gncOrganization.h"

#ifdef __cplusplus
extern "C" {
#endif

#ifndef _GNC_MOD_NAME
#define _GNC_MOD_NAME	GNC_ID_ORGANIZATION
#endif

#define GNC_ORGANIZATION_ID    "gncOrganization"
#define GNC_ORGANIZATION_TABLE "gncOrganization"

/** \struct GncOrganization */
struct _GncOrganization
{
    QofInstance   inst;
    const char *  id;
    const char *  name;
    const char *  notes;
    GncAddress *  addr;
    gnc_commodity * currency;
    gboolean      active;
    GList *       entities;   /* List of QofInstance entities belonging to this organization */
};

/** The organization properties
 */
enum
{
    PROP_0,
    PROP_ID,        
    PROP_NAME,      
    PROP_NOTES,     
    PROP_ACTIVE,    
};

void gncOrganizationBeginEdit (GncOrganization *organization);
void gncOrganizationCommitEdit (GncOrganization *organization);
void gncOrganizationFree (GncOrganization *organization);

#define SET_STR(obj, member, str) { \
        char * tmp; \
        \
        if (!safe_strcmp (member, str)) return; \
        gncOrganizationBeginEdit (obj); \
        tmp = CACHE_INSERT (str); \
        CACHE_REMOVE (member); \
        member = tmp; \
        }

#ifdef __cplusplus
}
#endif

#endif /* GNC_ORGANIZATIONP_H_ */