/** 
 * @file organization-multi-entity-demo.c
 * @brief Demonstration of enhanced multi-entity functionality for organizations
 * 
 * This demonstration shows how the new organization entity type enhances
 * the multi-entity aggregation system for organizational reporting and
 * business logic operations.
 */

/*
DEMONSTRATION SCENARIOS:

1. ORGANIZATIONAL STRUCTURE MANAGEMENT
   - Create an organization entity (e.g., "ACME Corporation")
   - Add various business entities (customers, vendors, employees) to the organization
   - Use multi-entity collections to work with all organization entities at once

2. ORGANIZATION-WIDE REPORTING
   - Create reports that span all entities within an organization
   - Filter multi-entity collections by organization membership
   - Aggregate financial data across all organization entities

3. MULTI-ENTITY OPERATIONS
   - Perform batch operations on all entities in an organization
   - Apply business rules across organizational boundaries
   - Support complex queries involving multiple entity types

ENHANCED FUNCTIONALITY:

// 1. Create and populate an organization
QofBook *book = qof_session_get_book(session);
GncOrganization *acme_corp = gncOrganizationCreate(book);
gncOrganizationSetName(acme_corp, "ACME Corporation");
gncOrganizationSetID(acme_corp, "ORG-001");

// Add business entities to the organization
GncCustomer *customer1 = gncCustomerCreate(book);
gncCustomerSetName(customer1, "BigCorp Client");
gncOrganizationAddEntity(acme_corp, QOF_INSTANCE(customer1));

GncVendor *vendor1 = gncVendorCreate(book);
gncVendorSetName(vendor1, "Office Supplies Inc");
gncOrganizationAddEntity(acme_corp, QOF_INSTANCE(vendor1));

GncEmployee *employee1 = gncEmployeeCreate(book);
gncEmployeeSetName(employee1, "John Doe");
gncOrganizationAddEntity(acme_corp, QOF_INSTANCE(employee1));

// 2. Create multi-entity collection from organization
QofMultiEntityCollection *org_entities = 
    qof_multi_entity_collection_from_organization(acme_corp);

printf("Organization '%s' has %u entities\n", 
       gncOrganizationGetName(acme_corp),
       qof_multi_entity_collection_count(org_entities));

// 3. Enhanced reporting across organization entities
QofMultiEntityCollection *all_entities = qof_multi_entity_collection_new();

// Add account and transaction collections
QofCollection *account_coll = qof_book_get_collection(book, "Account");
QofCollection *transaction_coll = qof_book_get_collection(book, "Transaction");
qof_multi_entity_collection_add_collection(all_entities, account_coll);
qof_multi_entity_collection_add_collection(all_entities, transaction_coll);

// Filter to only show entities belonging to our organization
QofMultiEntityCollection *org_filtered_entities = 
    qof_multi_entity_collection_filter_by_organization(all_entities, acme_corp);

// 4. Process organization entities
void process_organization_entity(QofInstance *entity, gpointer user_data) {
    const char *entity_type = QOF_INSTANCE_GET_TYPE_NAME(entity);
    const char *org_name = (const char *)user_data;
    
    printf("Processing %s entity for organization: %s\n", entity_type, org_name);
    
    // Perform organization-specific processing
    // (e.g., apply organization policies, generate reports, etc.)
}

const char *org_name = gncOrganizationGetName(acme_corp);
qof_multi_entity_collection_foreach(org_entities, process_organization_entity, 
                                     (gpointer)org_name);

// 5. Merge organization data with other collections for comprehensive reports
QofMultiEntityCollection *budget_entities = qof_multi_entity_collection_new();
QofCollection *budget_coll = qof_book_get_collection(book, "Budget");
qof_multi_entity_collection_add_collection(budget_entities, budget_coll);

// Add organization entities to budget collection for comprehensive analysis
qof_multi_entity_collection_add_organization_entities(budget_entities, acme_corp);

QofMultiEntityCollection *comprehensive_report = 
    qof_multi_entity_collection_merge(org_filtered_entities, budget_entities);

printf("Comprehensive report contains %u entities\n",
       qof_multi_entity_collection_count(comprehensive_report));

// 6. Organization-based entity statistics
guint org_entity_count = gncOrganizationGetEntityCount(acme_corp);
GList *entity_types = qof_multi_entity_collection_get_types(org_entities);
GList *type_node;

printf("Organization Statistics:\n");
printf("- Total entities: %u\n", org_entity_count);
printf("- Entity types: ");
for (type_node = entity_types; type_node; type_node = type_node->next) {
    printf("%s ", (char*)type_node->data);
}
printf("\n");

// Cleanup
qof_multi_entity_collection_destroy(org_entities);
qof_multi_entity_collection_destroy(all_entities);
qof_multi_entity_collection_destroy(org_filtered_entities);
qof_multi_entity_collection_destroy(budget_entities);
qof_multi_entity_collection_destroy(comprehensive_report);
g_list_free(entity_types);

BENEFITS OF ORGANIZATION ENHANCEMENTS:

1. HIERARCHICAL ENTITY MANAGEMENT
   - Organizations can contain multiple business entities
   - Supports complex organizational structures
   - Enables parent-child relationships between entities

2. ENHANCED REPORTING CAPABILITIES
   - Organization-wide financial reports
   - Cross-entity analysis and consolidation
   - Multi-entity performance metrics

3. IMPROVED BUSINESS LOGIC
   - Apply policies across organizational boundaries
   - Support for complex business rules
   - Streamlined workflow operations

4. SCALABILITY AND FLEXIBILITY
   - Handle large organizations with many entities
   - Support multiple organizational structures
   - Extensible for future entity types

5. DATA INTEGRITY AND CONSISTENCY
   - Maintain relationships between entities
   - Ensure data consistency across organizations
   - Support for audit trails and compliance

USE CASES FOR ORGANIZATIONS:

- Multi-subsidiary companies
- Holding companies with multiple businesses
- Franchises with multiple locations
- Professional services with multiple practice areas
- Non-profits with multiple programs
- Government agencies with multiple departments

This enhanced multi-entity functionality provides a robust foundation
for enterprise-level financial management and reporting systems.
*/