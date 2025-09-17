/** 
 * @file multi-entity-usage-example.c
 * @brief Example usage of the multi-entity aggregation feature
 * 
 * This example demonstrates how to use the QofMultiEntityCollection
 * to aggregate entities from multiple collections for reporting,
 * analysis, or unified operations.
 */

/*
EXAMPLE USAGE:

// Scenario: Create a report that aggregates accounts and transactions
// for a specific time period or matching certain criteria.

QofBook *book = qof_session_get_book(session);

// Get individual collections
QofCollection *account_coll = qof_book_get_collection(book, "Account");
QofCollection *transaction_coll = qof_book_get_collection(book, "Transaction");

// Create multi-entity collection for aggregation
QofMultiEntityCollection *report_entities = qof_multi_entity_collection_new();

// Aggregate all accounts
qof_multi_entity_collection_add_collection(report_entities, account_coll);

// Add filtered transactions (e.g., only from this year)
qof_multi_entity_collection_add_collection_filtered(report_entities, 
                                                     transaction_coll,
                                                     filter_current_year_transactions,
                                                     &current_year);

// Now you can iterate over all aggregated entities
qof_multi_entity_collection_foreach(report_entities, 
                                     process_entity_for_report, 
                                     report_data);

// Get statistics
guint total_entities = qof_multi_entity_collection_count(report_entities);
GList *types = qof_multi_entity_collection_get_types(report_entities);

// Filter for specific operations
QofMultiEntityCollection *high_value_entities = 
    qof_multi_entity_collection_filter(report_entities,
                                        filter_high_value_entities,
                                        &threshold_value);

// Merge with another aggregation
QofMultiEntityCollection *budget_entities = qof_multi_entity_collection_new();
qof_multi_entity_collection_add_collection(budget_entities, budget_coll);

QofMultiEntityCollection *complete_report = 
    qof_multi_entity_collection_merge(report_entities, budget_entities);

// Clean up
qof_multi_entity_collection_destroy(report_entities);
qof_multi_entity_collection_destroy(budget_entities);
qof_multi_entity_collection_destroy(high_value_entities);
qof_multi_entity_collection_destroy(complete_report);
g_list_free(types);

BENEFITS:
1. Unified interface for working with entities from multiple collections
2. Efficient filtering and aggregation without duplicating entities
3. Memory-efficient: stores references, not copies
4. Type-safe operations with proper error handling
5. Supports complex reporting scenarios
6. Maintains entity uniqueness (no duplicates)
7. Easy iteration and sorting capabilities

USE CASES:
- Financial reports spanning multiple entity types
- Search results aggregation
- Batch operations on mixed entity types
- Data export/import operations
- Business logic that needs to work across entity boundaries
- Performance optimization for complex queries
*/