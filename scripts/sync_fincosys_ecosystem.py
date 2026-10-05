#!/usr/bin/env python3
"""Sync gnucashm's multi-entity organizations into/out of the shared
fincosys ecosystem (accospace + fincosys + helix +
revstream1/ad-res-j7 case evidence).

This is the operational piece that was missing from the integration
documented in ORGANIZATION_ENHANCEMENTS.md /
libgnucash/engine/gnc-fincosys-sync.h: that bridge's
gnc_organizations_to_fincosys_json() / gnc_organizations_from_fincosys_json()
are C library functions exercised only by
libgnucash/engine/test/gtest-fincosys-sync.cpp -- nothing outside the test
suite actually builds a "fincosys-ecosystem-sync/v1" document. This script
drives the external accospace package (fincosys/accospace, formerly
RegimA-Zone/fincosys-atomspace-builder; the pip package and import name are
still fincosys-atomspace-builder / atomspace_builder -- only the repository
moved) -- the documented producer/consumer counterpart, see its README's
"GnuCash Ecosystem Sync" section -- to build the combined
"gnucash_ecosystem" AtomSpace from fincosys's master data plus helix's
ecosystem manifest and revstream1's case-evidence records, then writes out
the shared sync document as a staged input for
gnc_organizations_from_fincosys_json().

This script does not build or launch gnucashm itself. See
docs/FINCOSYS_ECOSYSTEM_SYNC.md for the current status of wiring that import
call up to a CLI/Scheme entry point, and for why helix's contribution to the
output is tagged unverified/self-reported rather than treated as financial
data.

By default this only builds the atomspace and prints a summary (--write is
required to persist anything under data/fincosys_sync/), and it only ever
touches the local JSON interchange format -- never Neon/R2 -- consistent
with this ecosystem's convention of gating real data-sync operations behind
explicit, reviewable steps (see cogpy/fincosys's CLAUDE.md, "Running-Balance
Continuity & Corpus Sync").
"""

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("sync_fincosys_ecosystem")

DEFAULT_OUTPUT = "data/fincosys_sync/gnucashm_ecosystem_sync.json"
DEFAULT_ATOMSPACE_JSON = "data/fincosys_sync/gnucashm_ecosystem_atomspace.json"
SOURCE_LABEL = "gnucashm"


#: Sibling-checkout directory names tried when --atomspace-builder-dir is
#: left at its default. "accospace" is the repository's current name;
#: "fincosys-atomspace-builder" is what it was called before the move to the
#: fincosys org, and existing checkouts still use it.
ATOMSPACE_BUILDER_DIRS = ("../accospace", "../fincosys-atomspace-builder")


#: The booking-side reader, reused rather than reimplemented. It already
#: knows which documents in the corpus are commerce *record* documents and
#: how to order two captures of the same record, and having one definition
#: of both in this repository is the point -- otherwise the hypergraph path
#: and the GnuCash-booking path can disagree about the same corpus.
COMMERCE_IMPORT_PATH = (
    Path(__file__).resolve().parent.parent
    / "bindings"
    / "python"
    / "example_scripts"
    / "fincosys_sync"
    / "commerce_import.py"
)


def _load_commerce_import():
    """Load commerce_import.py from its path, not through its package.

    `bindings/python/__init__.py` unconditionally does
    `from gnucash.gnucash_core import *`, so importing this module as
    `bindings.python.example_scripts...` pulls in the compiled `gnucash`
    extension as a side effect -- which this script never needs and which is
    usually absent in the environment it runs in. (The same hazard is why
    `fincosys_sync/pytest.ini` pins its own rootdir; see that file.) The
    module itself is pure standard library.
    """
    import importlib.util

    if not COMMERCE_IMPORT_PATH.exists():
        raise SystemExit(
            "commerce_import.py not found at {}. It ships in this "
            "repository; a checkout missing it is incomplete.".format(
                COMMERCE_IMPORT_PATH
            )
        )
    spec = importlib.util.spec_from_file_location(
        "fincosys_commerce_import", COMMERCE_IMPORT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_commerce_documents(record_paths, entity_repos_root, commerce_import=None):
    """Return (paths, skipped) -- the commerce documents to load, best first.

    Two things have to happen before accospace's `CommerceLoader` sees these
    paths, and neither is something the loader can do for itself.

    **Report captures carry this schema too.** Several QuickBooks documents
    in the corpus declare `fincosys-commerce-sync/v1` and hold a `report` or
    `items` block where a record document holds `records` -- a balance sheet,
    an AP aging detail, a product/service list. They are not record
    documents and they are not errors. `discover_documents` already draws
    that line, so it is reused here instead of being written a second time.

    **The corpus keeps superseded captures on purpose**, and the loader's
    de-duplication makes the order they arrive in load-bearing. It adds a
    record once (`if node_id not in hypergraph.nodes`) and walks its
    configured paths in *sorted* order, so the first document to carry a
    record wins and the winner is decided by filename. That is the wrong
    authority: `entity-rzl` holds both a 109-order Shopify window and the
    history that replaced it, and a partial capture whose name happens to
    sort earlier than the complete one that superseded it would set every
    shared record's confidence tier to `partial_capture` and keep it there.

    So the paths are ordered by the rule the booking side already uses to
    resolve a duplicate -- a complete capture beats a partial one, and among
    equals the later `generated_at` wins -- which turns the loader's
    first-wins into best-capture-wins without changing the loader. Ordering
    by path would make the answer depend on where someone happened to check
    the repositories out.
    """
    commerce_import = commerce_import or _load_commerce_import()

    candidates = []
    skipped = []
    if entity_repos_root:
        found, report_captures = commerce_import.discover_documents(entity_repos_root)
        candidates.extend(found)
        skipped.extend(report_captures)
    for entry in record_paths or []:
        path = Path(entry)
        if not path.exists():
            logger.warning("commerce record path does not exist: %s", path)
            continue
        if path.is_dir():
            found, report_captures = commerce_import.discover_documents(path)
            candidates.extend(found)
            skipped.extend(report_captures)
        else:
            candidates.append(str(path))

    # De-duplicate by resolved path: a document named explicitly *and*
    # discovered under a root is one document, and handing it to the loader
    # twice would be harmless but misreports the count.
    unique = {}
    for candidate in candidates:
        unique.setdefault(Path(candidate).resolve(), candidate)

    ranked = []
    for resolved, candidate in unique.items():
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            # Discovery already opened and parsed these, so reaching here
            # means the file changed under us. Report rather than drop.
            logger.warning("commerce document became unreadable: %s: %s", candidate, exc)
            continue
        ranked.append((commerce_import._supersession_rank(document, str(resolved)), candidate))

    # Descending, so the best capture is the first the loader sees.
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in ranked], sorted(skipped)


def audit_entity_codes(paths, known_codes=None):
    """Report the two ways a commerce document's entity code loses records.

    `entity.code` is the schema's join key: the loader attaches each record
    to the entity node the ecosystem-sync side already created from
    fincosys's master data. Both failures below are silent, which is why
    they get a pass of their own rather than a comment.

    **A code no entity node carries orphans every record in the document.**
    `CommerceLoader._link` returns without adding the edge when a node it
    needs is absent -- deliberately, because inventing an entity would
    fabricate one -- so the record nodes are created and attach to nothing.
    Nothing in the build says so.

    **One entity under two codes is counted twice.** Record ids embed the
    entity code (`QBO_RDH_INVOICE_12311` against `QBO_DRH_INVOICE_12311`),
    so neither the loader's node-id de-duplication nor the booking side's
    `resolve_duplicates` can see two captures of one invoice as the same
    record. They are two records and both are carried.

    The second test keys on **the entity's own identity**, not on the record
    id alone, and the distinction is the whole difference between a finding
    and a false alarm: a provider's document numbering is scoped to the
    provider account, so `QBO_RZI_INVOICE_100` and `QBO_RZL_INVOICE_100` are
    two unrelated invoices in two different QuickBooks companies and must
    not be reported. A collision is only a collision within one entity --
    same QBO realm, or failing that the same repository -- and that is what
    is counted here.

    Returns ``{"unknown_codes": {...}, "aliased_entities": [...]}``. This
    reports; it does not adjudicate. Which code is canonical is a question
    for fincosys's master data, and where two captures of one invoice
    disagree on the amount charged, choosing one would answer a question
    about the evidence silently.
    """
    unknown = {}
    anchors = {}
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            continue
        entity = document.get("entity") or {}
        code = entity.get("code")
        records = document.get("records")
        if not isinstance(records, list):
            continue
        if known_codes is not None and code not in known_codes:
            unknown.setdefault(code, []).append(path)
        if not code:
            continue

        provider = document.get("provider") or {}
        # `entity.repository` first, because the schema specifies it and
        # every document in the corpus carries it identically. The realm is
        # the fallback and needs both spellings: the corpus holds
        # `provider.realm_id` and `provider.realm` for the same field, and
        # keying on one of them splits an entity's documents into two
        # anchors -- which reads as no collision at all, the failure this
        # pass exists to catch.
        anchor = (
            entity.get("repository")
            or provider.get("realm_id")
            or provider.get("realm")
        )
        if not anchor:
            # Without a repository or a realm there is nothing to say two
            # codes are the same entity, and guessing from the legal name
            # is how a near-match becomes a merge.
            continue

        marker = "_%s_" % code
        suffixes = {
            (r.get("record_id").partition(marker)[0],
             r.get("record_id").partition(marker)[2])
            for r in records
            if (r or {}).get("record_id") and marker in r["record_id"]
        }
        slot = anchors.setdefault(anchor, {})
        slot.setdefault(code, set()).update(suffixes)

    aliased = []
    for anchor, by_code in sorted(anchors.items()):
        if len(by_code) < 2:
            continue
        codes = sorted(by_code)
        shared = set.intersection(*(by_code[c] for c in codes))
        aliased.append({
            "anchor": anchor,
            "codes": codes,
            "documents_carried_twice": len(shared),
            "records_per_code": {c: len(by_code[c]) for c in codes},
        })
    return {"unknown_codes": unknown, "aliased_entities": aliased}


def known_entity_codes(fincosys_data_dir):
    """Canonical fincosys entity codes, with their aliases, or None.

    None means the master data could not be read, which is different from
    "no codes": the caller must not then report every document's code as
    unknown. Aliases are included because `MASTER_ENTITIES.json` records
    them (`qbo_aliases`), and a document using one is a reportable defect
    rather than an unrecognised entity -- the distinction the audit draws.
    """
    master = Path(fincosys_data_dir) / "MASTER_ENTITIES.json"
    try:
        with open(master, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None

    entities = data if isinstance(data, list) else data.get("entities", data)
    if isinstance(entities, dict):
        entities = list(entities.values())
    if not isinstance(entities, list):
        return None

    codes = set()
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        code = entity.get("code") or entity.get("entity_code")
        if code:
            codes.add(code)
    return codes or None


def _resolve_atomspace_builder_dir(atomspace_builder_dir):
    """Return the accospace checkout to use, or None to try the defaults.

    An explicit --atomspace-builder-dir is honoured as given. Otherwise both
    the current and the former sibling directory names are tried, so a
    checkout made before the repository moved keeps working.
    """
    if atomspace_builder_dir is not None:
        return Path(atomspace_builder_dir).resolve()
    for candidate in ATOMSPACE_BUILDER_DIRS:
        path = Path(candidate).resolve()
        if path.exists():
            return path
    return None


def _add_atomspace_builder_to_path(atomspace_builder_dir) -> None:
    path = _resolve_atomspace_builder_dir(atomspace_builder_dir)
    if path is None or not path.exists():
        tried = (
            str(path)
            if path is not None
            else " or ".join(str(Path(d).resolve()) for d in ATOMSPACE_BUILDER_DIRS)
        )
        raise SystemExit(
            f"accospace checkout not found at {tried}. "
            "Clone https://github.com/fincosys/accospace as a sibling "
            "directory, or pass --atomspace-builder-dir. (The repository was "
            "formerly RegimA-Zone/fincosys-atomspace-builder; a checkout "
            "under that name is still accepted.)"
        )
    sys.path.insert(0, str(path))


def build_config(args, commerce_paths=None):
    from atomspace_builder.presets import gnucash_ecosystem_config

    # The preset has taken commerce_record_paths since the commerce schema
    # landed, and nothing on this side ever passed it -- so every ecosystem
    # sync document this script has produced carried organizations and atoms
    # and no QuickBooks or Shopify records at all, however many the entity
    # repositories held. Passing an empty list leaves include_commerce off,
    # exactly as before.
    config = gnucash_ecosystem_config(
        data_dir=args.fincosys_data_dir,
        commerce_record_paths=list(commerce_paths or []),
    )

    # This repo's own prior export feeds the "gnucashm" side of the merge in
    # loaders/gnucashm.py (updating existing entity/account nodes rather than
    # duplicating them). It's optional -- there may be no live book to export
    # from yet -- the loader degrades gracefully when it's absent.
    if args.gnucashm_export:
        config.gnucashm_export_path = args.gnucashm_export
    if args.gnucashcog_export:
        config.gnucashcog_export_path = args.gnucashcog_export
    if args.helix_manifest:
        config.helix_manifest_path = args.helix_manifest

    if args.revstream1_data_dir:
        config.include_case_evidence = True
        config.revstream1_data_dir = args.revstream1_data_dir

    if args.entity_filter:
        config.entity_filter = args.entity_filter

    return config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--atomspace-builder-dir",
        default=None,
        help="Path to an accospace (fincosys/accospace) checkout. Default: "
        "the first of "
        + ", ".join(ATOMSPACE_BUILDER_DIRS)
        + " that exists -- the latter being the repository's former name.",
    )
    parser.add_argument(
        "--fincosys-data-dir",
        default="../fincosys/data",
        help="Path to fincosys's data/ directory (default: %(default)s)",
    )
    parser.add_argument(
        "--gnucashm-export",
        help="Path to a prior gnc_organizations_to_fincosys_json() export "
        "(default: looked up under --fincosys-data-dir)",
    )
    parser.add_argument(
        "--gnucashcog-export",
        help="Path to a gnucashcog-v3 gnc_cognitive_export_fincosys_json() export",
    )
    parser.add_argument(
        "--helix-manifest",
        default="../helix/ecosystem/related_artifacts.json",
        help="Path to helix's ecosystem/related_artifacts.json manifest -- "
        "self-reported/unverified, see docs/FINCOSYS_ECOSYSTEM_SYNC.md "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--revstream1-data-dir",
        default="../revstream1/data_models",
        help="Path to a revstream1 checkout's data_models/ directory, for "
        "evidence_refs/legal_categories case-evidence enrichment "
        "(default: %(default)s; pass --no-case-evidence to skip)",
    )
    parser.add_argument(
        "--no-case-evidence",
        action="store_true",
        help="Skip revstream1 case-evidence enrichment even if --revstream1-data-dir exists",
    )
    parser.add_argument(
        "--commerce-records",
        action="append",
        metavar="PATH",
        help="A fincosys-commerce-sync/v1 document, or a directory holding "
        "them, to load alongside the bank-statement corpus. Repeatable. "
        "QuickBooks and Shopify records are not bank transactions -- a "
        "statement says money moved, these say why -- and they are loaded "
        "with their own node and edge types so they can never be "
        "double-counted against the balance-verified statement corpus.",
    )
    parser.add_argument(
        "--entity-repos-root",
        metavar="DIR",
        help="A directory of fincosys entity-repository checkouts to scan "
        "for commerce documents, instead of (or as well as) naming them. "
        "The corpus is 40-odd repositories and a capture named by hand is a "
        "capture that can be left out silently.",
    )
    parser.add_argument(
        "-e",
        "--entity-filter",
        nargs="+",
        help="Restrict to specific fincosys entity codes",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Where to write the fincosys-ecosystem-sync/v1 document (default: %(default)s)",
    )
    parser.add_argument(
        "--atomspace-json",
        default=DEFAULT_ATOMSPACE_JSON,
        help="Where to also write the full atomspace JSON export, for inspection "
        "(default: %(default)s; pass an empty string to skip)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist the output file(s). Without this flag the script only "
        "builds the atomspace and prints a summary (dry run).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.no_case_evidence:
        args.revstream1_data_dir = None
    elif not Path(args.revstream1_data_dir).exists():
        logger.warning(
            "revstream1_data_dir %s not found; skipping case-evidence enrichment",
            args.revstream1_data_dir,
        )
        args.revstream1_data_dir = None

    if args.helix_manifest and not Path(args.helix_manifest).exists():
        logger.warning(
            "helix manifest %s not found; helix provenance nodes will be skipped",
            args.helix_manifest,
        )
        args.helix_manifest = None

    commerce_paths, commerce_skipped = select_commerce_documents(
        args.commerce_records, args.entity_repos_root
    )
    if args.commerce_records or args.entity_repos_root:
        if not commerce_paths:
            logger.warning(
                "no commerce record documents found; the sync document will "
                "carry no QuickBooks/Shopify records"
            )
        else:
            logger.info(
                "commerce: %d record document(s), best capture first",
                len(commerce_paths),
            )
        if commerce_skipped:
            logger.info(
                "commerce: skipped %d document(s) declaring the schema but "
                "holding a report/items block rather than records",
                len(commerce_skipped),
            )

        audit = audit_entity_codes(
            commerce_paths, known_entity_codes(args.fincosys_data_dir)
        )
        for code, where in sorted(audit["unknown_codes"].items(), key=lambda kv: str(kv[0])):
            logger.warning(
                "commerce: entity.code %r is not a canonical fincosys entity "
                "code; every record in %d document(s) will be created and then "
                "attach to no entity, because the loader drops an edge whose "
                "entity node is absent rather than inventing one: %s",
                code, len(where), ", ".join(sorted(where)),
            )
        for entry in audit["aliased_entities"]:
            logger.warning(
                "commerce: one entity (%s) is claimed by %d entity codes "
                "(%s), and %d of its provider documents are carried under "
                "more than one. Record ids embed the code, so neither this "
                "loader nor the booking side can see those as one record. "
                "Which code is canonical is a question for fincosys's master "
                "data, not for this script.",
                entry["anchor"], len(entry["codes"]), ", ".join(entry["codes"]),
                entry["documents_carried_twice"],
            )

    _add_atomspace_builder_to_path(args.atomspace_builder_dir)

    from atomspace_builder.builder import AtomSpaceBuilder

    config = build_config(args, commerce_paths)
    issues = config.validate()
    for issue in issues:
        logger.warning("config: %s", issue)

    builder = AtomSpaceBuilder(config)
    atomspace = builder.build()

    commerce_nodes = sum(
        1
        for node in atomspace.nodes.values()
        if (getattr(node, "attributes", None) or {}).get("commerce_record")
    )

    print(f"Built {config.name}")
    print(f"  Nodes: {len(atomspace.nodes)}")
    print(f"  Edges: {len(atomspace.edges)}")
    print(f"  Rules: {len(atomspace.rules)}")
    print(f"  Commerce records: {commerce_nodes} "
          f"(from {len(commerce_paths)} document(s))")

    if not args.write:
        print("\nDry run (pass --write to persist output files).")
        return 0

    from atomspace_builder.exporters import EcosystemSyncExporter, JsonExporter

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    EcosystemSyncExporter(args.output, source=SOURCE_LABEL).export(atomspace)
    print(f"Wrote ecosystem-sync document: {args.output}")
    print(
        "  -> import into a gnucashm book with "
        "gnc_organizations_from_fincosys_json() (see "
        "libgnucash/engine/gnc-fincosys-sync.h)"
    )

    if args.atomspace_json:
        Path(args.atomspace_json).parent.mkdir(parents=True, exist_ok=True)
        builder.export(JsonExporter(args.atomspace_json))
        print(f"Wrote full atomspace JSON: {args.atomspace_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
