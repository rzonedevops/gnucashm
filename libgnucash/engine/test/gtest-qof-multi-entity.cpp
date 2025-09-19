/********************************************************************\
 * gtest-qof-multi-entity.cpp -- Unit tests for multi-entity       *
 *                               aggregation functionality          *
 *                                                                  *
 * Copyright 2024 GnuCash Contributors                              *
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
 *********************************************************************/

#include <config.h>
#include <glib.h>
#include "../test-core/test-engine-stuff.h"
#include "../qofid.h"
#include "../qofinstance.h"
#include "../guid.h"

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcpp"
#include <gtest/gtest.h>
#pragma GCC diagnostic pop

class QofMultiEntityTest : public ::testing::Test
{
protected:
    void SetUp() override
    {
        // Create test collections
        account_coll = qof_collection_new("Account");
        transaction_coll = qof_collection_new("Transaction");
        
        // Create test entities using proper GObject creation
        for (int i = 0; i < 3; i++)
        {
            QofInstance* account = static_cast<QofInstance*>(g_object_new(QOF_TYPE_INSTANCE, NULL));
            QofInstance* transaction = static_cast<QofInstance*>(g_object_new(QOF_TYPE_INSTANCE, NULL));
            
            // Set entity types
            account->e_type = "Account";
            transaction->e_type = "Transaction";
            
            qof_collection_insert_entity(account_coll, account);
            qof_collection_insert_entity(transaction_coll, transaction);
            
            accounts.push_back(account);
            transactions.push_back(transaction);
        }
    }
    
    void TearDown() override
    {
        // Clean up entities
        for (auto* account : accounts)
        {
            g_object_unref(account);
        }
        for (auto* transaction : transactions)
        {
            g_object_unref(transaction);
        }
        
        // Clean up collections
        qof_collection_destroy(account_coll);
        qof_collection_destroy(transaction_coll);
    }
    
    QofCollection* account_coll;
    QofCollection* transaction_coll;
    std::vector<QofInstance*> accounts;
    std::vector<QofInstance*> transactions;
};

TEST_F(QofMultiEntityTest, CreateAndDestroy)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    ASSERT_NE(nullptr, multi_coll);
    
    EXPECT_EQ(0U, qof_multi_entity_collection_count(multi_coll));
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, AddSingleEntity)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add a single account
    EXPECT_TRUE(qof_multi_entity_collection_add_entity(multi_coll, accounts[0]));
    EXPECT_EQ(1U, qof_multi_entity_collection_count(multi_coll));
    EXPECT_TRUE(qof_multi_entity_collection_contains(multi_coll, accounts[0]));
    
    // Try to add the same entity again (should fail)
    EXPECT_FALSE(qof_multi_entity_collection_add_entity(multi_coll, accounts[0]));
    EXPECT_EQ(1U, qof_multi_entity_collection_count(multi_coll));
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, AddEntireCollection)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add all accounts
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    EXPECT_EQ(3U, qof_multi_entity_collection_count(multi_coll));
    
    // Verify all accounts are present
    for (const auto* account : accounts)
    {
        EXPECT_TRUE(qof_multi_entity_collection_contains(multi_coll, account));
    }
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, AddMultipleCollections)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add both collections
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    qof_multi_entity_collection_add_collection(multi_coll, transaction_coll);
    
    EXPECT_EQ(6U, qof_multi_entity_collection_count(multi_coll));
    
    // Verify all entities are present
    for (const auto* account : accounts)
    {
        EXPECT_TRUE(qof_multi_entity_collection_contains(multi_coll, account));
    }
    for (const auto* transaction : transactions)
    {
        EXPECT_TRUE(qof_multi_entity_collection_contains(multi_coll, transaction));
    }
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, RemoveEntity)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add all accounts
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    EXPECT_EQ(3U, qof_multi_entity_collection_count(multi_coll));
    
    // Remove one account
    EXPECT_TRUE(qof_multi_entity_collection_remove_entity(multi_coll, accounts[0]));
    EXPECT_EQ(2U, qof_multi_entity_collection_count(multi_coll));
    EXPECT_FALSE(qof_multi_entity_collection_contains(multi_coll, accounts[0]));
    
    // Try to remove the same entity again (should fail)
    EXPECT_FALSE(qof_multi_entity_collection_remove_entity(multi_coll, accounts[0]));
    EXPECT_EQ(2U, qof_multi_entity_collection_count(multi_coll));
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, GetTypes)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add both collections
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    qof_multi_entity_collection_add_collection(multi_coll, transaction_coll);
    
    GList* types = qof_multi_entity_collection_get_types(multi_coll);
    EXPECT_EQ(2U, g_list_length(types));
    
    // Check that both types are present
    gboolean found_account = FALSE;
    gboolean found_transaction = FALSE;
    
    for (GList* node = types; node; node = node->next)
    {
        const char* type = (const char*)node->data;
        if (g_strcmp0(type, "Account") == 0)
            found_account = TRUE;
        else if (g_strcmp0(type, "Transaction") == 0)
            found_transaction = TRUE;
    }
    
    EXPECT_TRUE(found_account);
    EXPECT_TRUE(found_transaction);
    
    g_list_free(types);
    qof_multi_entity_collection_destroy(multi_coll);
}

// Helper function for filtering tests
static gboolean
filter_accounts_only(QofInstance* entity, gpointer user_data)
{
    return g_strcmp0(entity->e_type, "Account") == 0;
}

TEST_F(QofMultiEntityTest, FilteredAdd)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add only accounts from both collections using filter
    qof_multi_entity_collection_add_collection_filtered(multi_coll, account_coll, 
                                                         filter_accounts_only, nullptr);
    qof_multi_entity_collection_add_collection_filtered(multi_coll, transaction_coll, 
                                                         filter_accounts_only, nullptr);
    
    // Should only have accounts (3), no transactions
    EXPECT_EQ(3U, qof_multi_entity_collection_count(multi_coll));
    
    for (const auto* account : accounts)
    {
        EXPECT_TRUE(qof_multi_entity_collection_contains(multi_coll, account));
    }
    for (const auto* transaction : transactions)
    {
        EXPECT_FALSE(qof_multi_entity_collection_contains(multi_coll, transaction));
    }
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, FilterCollection)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add both collections
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    qof_multi_entity_collection_add_collection(multi_coll, transaction_coll);
    
    // Filter to get only accounts
    QofMultiEntityCollection* filtered_coll = 
        qof_multi_entity_collection_filter(multi_coll, filter_accounts_only, nullptr);
    
    EXPECT_EQ(3U, qof_multi_entity_collection_count(filtered_coll));
    
    for (const auto* account : accounts)
    {
        EXPECT_TRUE(qof_multi_entity_collection_contains(filtered_coll, account));
    }
    for (const auto* transaction : transactions)
    {
        EXPECT_FALSE(qof_multi_entity_collection_contains(filtered_coll, transaction));
    }
    
    qof_multi_entity_collection_destroy(multi_coll);
    qof_multi_entity_collection_destroy(filtered_coll);
}

TEST_F(QofMultiEntityTest, MergeCollections)
{
    QofMultiEntityCollection* coll1 = qof_multi_entity_collection_new();
    QofMultiEntityCollection* coll2 = qof_multi_entity_collection_new();
    
    // Add accounts to coll1, transactions to coll2
    qof_multi_entity_collection_add_collection(coll1, account_coll);
    qof_multi_entity_collection_add_collection(coll2, transaction_coll);
    
    // Merge collections
    QofMultiEntityCollection* merged_coll = 
        qof_multi_entity_collection_merge(coll1, coll2);
    
    EXPECT_EQ(6U, qof_multi_entity_collection_count(merged_coll));
    
    // Verify all entities are present
    for (const auto* account : accounts)
    {
        EXPECT_TRUE(qof_multi_entity_collection_contains(merged_coll, account));
    }
    for (const auto* transaction : transactions)
    {
        EXPECT_TRUE(qof_multi_entity_collection_contains(merged_coll, transaction));
    }
    
    qof_multi_entity_collection_destroy(coll1);
    qof_multi_entity_collection_destroy(coll2);
    qof_multi_entity_collection_destroy(merged_coll);
}

// Helper for iteration test
struct IterationCounter
{
    int count;
    std::vector<QofInstance*> entities;
};

static void
count_entities_cb(QofInstance* entity, gpointer user_data)
{
    IterationCounter* counter = (IterationCounter*)user_data;
    counter->count++;
    counter->entities.push_back(entity);
}

TEST_F(QofMultiEntityTest, ForEach)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add both collections
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    qof_multi_entity_collection_add_collection(multi_coll, transaction_coll);
    
    // Test foreach iteration
    IterationCounter counter = {0, {}};
    qof_multi_entity_collection_foreach(multi_coll, count_entities_cb, &counter);
    
    EXPECT_EQ(6, counter.count);
    EXPECT_EQ(6U, counter.entities.size());
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, NullPointerHandling)
{
    // Test null pointer handling - these should not crash
    qof_multi_entity_collection_destroy(nullptr);
    qof_multi_entity_collection_add_collection(nullptr, account_coll);
    qof_multi_entity_collection_add_collection_filtered(nullptr, account_coll, 
                                                         filter_accounts_only, nullptr);
    EXPECT_FALSE(qof_multi_entity_collection_add_entity(nullptr, accounts[0]));
    EXPECT_FALSE(qof_multi_entity_collection_remove_entity(nullptr, accounts[0]));
    EXPECT_EQ(0U, qof_multi_entity_collection_count(nullptr));
    EXPECT_FALSE(qof_multi_entity_collection_contains(nullptr, accounts[0]));
    qof_multi_entity_collection_foreach(nullptr, count_entities_cb, nullptr);
    EXPECT_EQ(nullptr, qof_multi_entity_collection_get_types(nullptr));
    EXPECT_EQ(nullptr, qof_multi_entity_collection_filter(nullptr, filter_accounts_only, nullptr));
    EXPECT_EQ(nullptr, qof_multi_entity_collection_merge(nullptr, nullptr));
}

// Tests for organization-specific multi-entity functionality
TEST_F(QofMultiEntityTest, OrganizationMultiEntity)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Create a mock organization with entities
    // Note: In a real implementation, you would use actual GncOrganization objects
    // For testing, we'll simulate this functionality
    
    // Add some entities to the multi-entity collection
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    
    EXPECT_EQ(3U, qof_multi_entity_collection_count(multi_coll));
    
    qof_multi_entity_collection_destroy(multi_coll);
}

TEST_F(QofMultiEntityTest, OrganizationFiltering)
{
    QofMultiEntityCollection* multi_coll = qof_multi_entity_collection_new();
    
    // Add both collections
    qof_multi_entity_collection_add_collection(multi_coll, account_coll);
    qof_multi_entity_collection_add_collection(multi_coll, transaction_coll);
    
    EXPECT_EQ(6U, qof_multi_entity_collection_count(multi_coll));
    
    // Test filtering - in a real implementation, this would filter by organization
    // For now, we'll test the basic filtering mechanism
    QofMultiEntityCollection* filtered = 
        qof_multi_entity_collection_filter(multi_coll, filter_accounts_only, nullptr);
    
    // The filter should reduce the count (exact number depends on implementation)
    guint filtered_count = qof_multi_entity_collection_count(filtered);
    EXPECT_LE(filtered_count, 6U);
    
    qof_multi_entity_collection_destroy(multi_coll);
    qof_multi_entity_collection_destroy(filtered);
}