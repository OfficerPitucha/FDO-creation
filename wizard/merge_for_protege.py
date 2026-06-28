#!/usr/bin/env python3
"""Flatten a bundled FDO TriG and merge it with the pinned FDOF ontologies
into a single Turtle file that Protege (or any OWL-API tool) can open.

Protege reads one RDF graph, not a TriG dataset with named graphs, and it
needs the published FDOF-O/FDOF-T axioms in scope for the reasoner to check
consistency. This script produces that single self-contained file.

Usage:
    python merge_for_protege.py ../out/<slug>/<slug>.trig
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rdflib import Dataset, Graph

HERE = Path(__file__).parent
ONTOLOGY_PATHS = [HERE / "fdof-o.ttl", HERE / "fdof-t.ttl"]


def merge_for_protege(trig_path: Path, out_path: Path) -> tuple[int, int]:
    """Build the merged Turtle file. Returns (data_triples, total_triples)."""
    # 1. load the FDO (TriG, named graphs) and flatten to triples
    ds = Dataset()
    ds.parse(str(trig_path), format="trig")
    merged = Graph()
    for s, p, o, _g in ds.quads((None, None, None, None)):
        merged.add((s, p, o))
    data_triples = len(merged)

    # 2. add the pinned ontologies (class hierarchy + axioms for the reasoner)
    for ont in ONTOLOGY_PATHS:
        merged.parse(str(ont), format="turtle")

    # 3. write the single file Protege will open
    merged.serialize(str(out_path), format="turtle")
    return data_triples, len(merged)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Flatten an FDO TriG + merge the FDOF ontologies into one "
                    "Turtle file for Protege.")
    ap.add_argument("trig", type=Path, help="Path to the bundled <slug>.trig.")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="Output .ttl path "
                         "(default: <slug>-for-protege.ttl next to the input).")
    args = ap.parse_args()

    if not args.trig.is_file():
        raise SystemExit(f"Input TriG not found: {args.trig}")
    out_path = args.output or args.trig.with_name(
        args.trig.stem + "-for-protege.ttl")

    data_triples, total = merge_for_protege(args.trig, out_path)
    print(f"  flattened {data_triples} FDO triples + ontologies "
          f" {total} total")
    print(f"  wrote {out_path}")


if __name__ == "__main__":
    main()
