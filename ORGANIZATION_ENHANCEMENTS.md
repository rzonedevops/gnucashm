# Multi-Entity Functionality Enhancements for Organizations

## Overview

This enhancement adds comprehensive organization support to GnuCash's multi-entity aggregation system, enabling advanced organizational management and reporting capabilities.

## Key Enhancements

### 1. Organization Entity Type
- **New GncOwnerType**: Added `GNC_OWNER_ORGANIZATION` to the owner enumeration
- **Complete Organization Object**: Full QOF-compliant GncOrganization entity with:
  - ID, name, notes, address, currency, and active status
  - Entity management (add/remove business entities)
  - QOF integration for database operations

### 2. Enhanced Multi-Entity Collections
- **Organization-specific functions**:
  - `qof_multi_entity_collection_from_organization()` - Create collection from org entities
  - `qof_multi_entity_collection_add_organization_entities()` - Add org entities to collection
  - `qof_multi_entity_collection_filter_by_organization()` - Filter by organization membership

### 3. Owner System Integration
- Updated `GncOwner` structure to include organization pointer
- Added organization cases to key owner functions:
  - Type string conversion
  - ID and name retrieval
  - QOF type mapping
  - Begin/commit edit operations
  - Destroy operations

### 4. Testing and Documentation
- Extended unit tests for organization functionality
- Comprehensive usage examples and demonstration code
- Updated documentation with organization scenarios

## Benefits

### Hierarchical Entity Management
- Organizations can contain multiple business entities (customers, vendors, employees)
- Supports complex organizational structures
- Enables parent-child relationships between entities

### Enhanced Reporting Capabilities
- Organization-wide financial reports
- Cross-entity analysis and consolidation
- Multi-entity performance metrics
- Filtered reporting by organizational membership

### Improved Business Logic
- Apply policies across organizational boundaries
- Support for complex business rules involving multiple entity types
- Streamlined workflow operations across organization entities

### Scalability and Flexibility
- Handle large organizations with many entities
- Support multiple organizational structures
- Extensible foundation for future entity types

## Use Cases

### Enterprise Scenarios
- **Multi-subsidiary companies**: Manage parent company with multiple subsidiaries
- **Holding companies**: Oversee multiple business units with separate entities
- **Franchises**: Handle franchise operations with multiple locations
- **Professional services**: Manage different practice areas or departments

### Organizational Reporting
- **Consolidated financial statements**: Aggregate data across all organization entities
- **Cross-entity analysis**: Compare performance between different organizational units
- **Compliance reporting**: Generate organization-wide reports for regulatory requirements
- **Budget management**: Plan and track budgets across organizational boundaries

### Workflow Enhancements
- **Batch operations**: Apply changes to all entities within an organization
- **Policy enforcement**: Ensure consistent application of business rules
- **Data synchronization**: Maintain consistency across related entities

## Technical Implementation

### Core Files Modified/Added
- `gncOrganization.h/c` - New organization entity implementation
- `gncOrganizationP.h` - Private organization definitions
- `gncOwner.h/c` - Enhanced owner system with organization support
- `qofid.h/cpp` - Multi-entity collection enhancements
- `gnc-fincosys-sync.h/cpp` - Fincosys ecosystem sync bridge (see below)
- `CMakeLists.txt` - Build system integration

### QOF Integration
- Full QOF object registration for organizations
- Property system integration
- Database backend support preparation
- Event system integration

### Memory Management
- Proper reference counting and cleanup
- Efficient entity storage using hash tables
- Memory-efficient entity referencing (not copying)

## Future Enhancements

### Potential Extensions
1. **Nested Organizations**: Support for organization hierarchies
2. **Role-based Access**: Different access levels within organizations
3. **Organization Templates**: Predefined organizational structures
4. **Workflow Integration**: Organization-aware business processes
5. **Advanced Reporting**: Drill-down and roll-up reporting capabilities

### Database Schema
The organization entity is designed to be compatible with existing GnuCash database backends and can be extended to support:
- SQL database storage
- XML serialization
- Network synchronization -- **implemented** (see below)
- Backup and restore operations

## Fincosys Ecosystem Sync

The "Network synchronization" extension point above is now partially implemented:
`libgnucash/engine/gnc-fincosys-sync.h/cpp` serializes/parses
`QofMultiEntityCollection` / `GncOrganization` data to and from the shared
**Fincosys Ecosystem Sync Schema v1** (`"schema": "fincosys-ecosystem-sync/v1"`)
used across the wider financial-ecosystem tooling:

- `gnc_organizations_to_fincosys_json(GList *organizations)` walks a list of
  `GncOrganization*` and their `Account` entities and returns a JSON document
  (`"source": "gnucashm"`) with `organizations[].accounts[]` entries.
- `gnc_organizations_from_fincosys_json(QofBook *book, const gchar *json)`
  parses a document in the same schema (e.g. produced by
  `fincosys-atomspace-builder`'s `EcosystemSyncExporter`, or by fincosys
  itself) and creates a `GncOrganization` plus placeholder `Account` entries
  per organization.

The consumer/producer side of this bridge lives in the
[`fincosys-atomspace-builder`](https://github.com/RegimA-Zone/fincosys-atomspace-builder)
repository: `atomspace_builder/loaders/gnucashm.py` reads gnucashm's export
and merges it into a built AtomSpace hypergraph (updating existing
entity/account nodes loaded from fincosys's `MASTER_ENTITIES.json` rather
than duplicating them), and `EcosystemSyncExporter` produces documents this
importer can read back. See that repo's README for the full schema and the
`gnucashcog-v3` / `helix` sides of the same sync loop.

The JSON (de)serialization in `gnc-fincosys-sync.cpp` is a small,
purpose-built parser/writer scoped to this schema's shape -- it is not a
general-purpose JSON library, and does not pull in a new external
dependency.

**Verified end-to-end (2026-07-23)**, including a real `gnucash-cli
--import-fincosys-sync` run: see `docs/FINCOSYS_ECOSYSTEM_SYNC.md` for the
build results, the orphan-account persistence bug that run found and
fixed, and the still-open `GncOrganization` XML-backend gap (organization
metadata itself -- as opposed to its accounts -- has no persistence path
yet).

Unrelated to this bridge: while building and testing this change, the
existing `test-qof-multi-entity` gtest suite (`gtest-qof-multi-entity.cpp`)
was found to have 11/13 pre-existing failures against the current
`QofMultiEntityCollection` implementation, reproducible on a clean checkout
of this branch prior to these changes. That regression is out of scope
here (this change only adds new files plus the `gncOrganizationGetEntities`
read path already exercised in `gnc-fincosys-sync.cpp`'s own passing test
suite) but is worth a follow-up investigation.

## Conclusion

These enhancements significantly expand GnuCash's capability to handle complex organizational structures while maintaining backward compatibility. The multi-entity aggregation system now provides a robust foundation for enterprise-level financial management and reporting, enabling users to efficiently manage and analyze data across multiple business entities within organizational contexts.

The implementation follows GnuCash's established patterns and conventions, ensuring seamless integration with existing functionality while providing powerful new capabilities for organizational management.