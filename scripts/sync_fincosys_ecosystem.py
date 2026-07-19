#!/usr/bin/env python3
"""Sync gnucashm's multi-entity organizations into/out of the shared
fincosys ecosystem (fincosys-atomspace-builder + fincosys + helix +
revstream1/ad-res-j7 case evidence).

This is the operational piece that was missing from the integration
documented in ORGANIZATION_ENHANCEMENTS.md /
libgnucash/engine/gnc-fincosys-sync.h: that bridge's
gnc_organizations_to_fincosys_json() / gnc_organizations_from_fincosys_json()
are C library functions exercised only by
libgnucash/engine/test/gtest-fincosys-sync.cpp -- nothing outside the test
suite actually builds a "fincosys-ecosystem-sync/v1" document. This script
drives the external fincosys-atomspace-builder package (the documented
producer/consumer counterpart, see its README's "GnuCash Ecosystem Sync"
section) to build the combined "gnucash_ecosystem" AtomSpace from fincosys's
master data plus helix's ecosystem manifest and revstream1's case-evidence
records, then writes out the shared sync document as a staged input for
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
import logging
import sys
from pathlib import Path

logger = logging.getLogger("sync_fincosys_ecosystem")

DEFAULT_OUTPUT = "data/fincosys_sync/gnucashm_ecosystem_sync.json"
DEFAULT_ATOMSPACE_JSON = "data/fincosys_sync/gnucashm_ecosystem_atomspace.json"
SOURCE_LABEL = "gnucashm"


def _add_atomspace_builder_to_path(atomspace_builder_dir: str) -> None:
    path = Path(atomspace_builder_dir).resolve()
    if not path.exists():
        raise SystemExit(
            f"fincosys-atomspace-builder checkout not found at {path}. "
            "Clone https://github.com/RegimA-Zone/fincosys-atomspace-builder "
            "as a sibling directory, or pass --atomspace-builder-dir."
        )
    sys.path.insert(0, str(path))


def build_config(args):
    from atomspace_builder.presets import gnucash_ecosystem_config

    config = gnucash_ecosystem_config(data_dir=args.fincosys_data_dir)

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
        default="../fincosys-atomspace-builder",
        help="Path to a fincosys-atomspace-builder checkout (default: %(default)s)",
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

    _add_atomspace_builder_to_path(args.atomspace_builder_dir)

    from atomspace_builder.builder import AtomSpaceBuilder

    config = build_config(args)
    issues = config.validate()
    for issue in issues:
        logger.warning("config: %s", issue)

    builder = AtomSpaceBuilder(config)
    atomspace = builder.build()

    print(f"Built {config.name}")
    print(f"  Nodes: {len(atomspace.nodes)}")
    print(f"  Edges: {len(atomspace.edges)}")
    print(f"  Rules: {len(atomspace.rules)}")

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
