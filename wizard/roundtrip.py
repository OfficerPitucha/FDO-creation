#!/usr/bin/env python3
"""Round-trip reconstructability test.

Given the REST-path resolution model, verify that a consumer starting from only the FDIO's id can
discover and retrieve every object in the FDO's inventory.

The consumer is modelled faithfully to that resolution model:

  * It holds a set of known object ids. It starts knowing only the FDIO.
  * For each known id it resolves the records the suffix convention provides -
    identifierRecord (always) and metadataRecord (when present) - and adds
    their triples to its accumulated knowledge.
  * From that knowledge it discovers new objects by following the two FDOF
    structural predicates the proposal names: forward `fdof:isMaterializedBy`
    (object -> its materialising FDMO) and inverse `fdof:isMetadataOf`
    (object <- the FMR describing it).
  * It repeats to a fixpoint.

This is NOT a critique of the REST-path design (the suffix convention is
assumed). It is a correctness check on the *generated files*: do the emitted
identifier/metadata records carry the cross-object links the resolution model
relies on, and does every link target resolve to a served object? Real failure
modes it catches:

  * an FDMO (e.g. the bundled-TriG FDMO) minted but not linked -> unreachable;
  * a renamed object whose link still points at the old local name -> dangling;
  * the optional metametadata object added without its discovery link;
  * a link target IRI that does not correspond to a servable id.

Usage:
    python roundtrip.py [SLUG-DIR ...]      # default: all FDOs under output directory
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Dataset, Graph, Namespace, URIRef
from rdflib.namespace import RDF

from split_for_server import FDOF

HERE = Path(__file__).parent
DEFAULT_ROOT = HERE.parent / "out"
FDOF_NS = Namespace(FDOF)

IS_MATERIALIZED_BY = FDOF_NS.isMaterializedBy
IS_METADATA_OF = FDOF_NS.isMetadataOf
FAIR_INFO_OBJECT = FDOF_NS.FAIRInformationObject
FAIR_METADATA_RECORD = FDOF_NS.FAIRMetadataRecord
RECORD_STEMS = ("identifierRecord", "metadataRecord")


def _read_records(obj_dir: Path) -> Graph:
    """Union of all triples in the object's resolvable records."""
    g = Graph()
    for stem in RECORD_STEMS:
        for path in obj_dir.glob(f"{stem}.*"):
            ds = Dataset()
            ds.parse(str(path), format="trig")
            for graph in ds.graphs():
                for triple in graph:
                    g.add(triple)
    return g


def _served_objects(fdo_dir: Path) -> dict[str, Path]:
    """Served object ids -> directory (those exposing an identifierRecord)."""
    out: dict[str, Path] = {}
    for d in sorted(p for p in fdo_dir.iterdir() if p.is_dir()):
        if list(d.glob("identifierRecord.*")):
            out[d.name] = d
    return out


def _find_fdio(served: dict[str, Path]) -> tuple[str | None, str]:
    """The FDIO id (FAIRInformationObject, not a FAIRMetadataRecord) and the
    namespace base of the FDO's local identifiers."""
    for name, d in served.items():
        g = _read_records(d)
        for s in g.subjects(RDF.type, FAIR_INFO_OBJECT):
            if not isinstance(s, URIRef):
                continue
            if (s, RDF.type, FAIR_METADATA_RECORD) in g:
                continue
            if "#" in str(s):
                ns_base = str(s).rsplit("#", 1)[0] + "#"
                return name, ns_base
    return None, ""


def check_roundtrip(fdo_dir: Path):
    """Return (ok, reached, served, mechanism, dangling)."""
    served = _served_objects(fdo_dir)
    fdio, ns_base = _find_fdio(served)
    if fdio is None:
        return False, set(), served, {}, []

    def local(iri: URIRef) -> str:
        s = str(iri)
        return s[len(ns_base):] if s.startswith(ns_base) else s.rsplit("#", 1)[-1]

    def iri(name: str) -> URIRef:
        return URIRef(ns_base + name)

    reached: set[str] = {fdio}
    mechanism: dict[str, str] = {fdio: "entry point"}
    dangling: list[tuple[str, str, str]] = []
    fetched: set[str] = set()
    knowledge = Graph()

    while True:
        # Resolve (by suffix) the records of every object known but not yet
        # fetched, accumulating their triples.
        for name in list(reached):
            if name not in fetched:
                knowledge += _read_records(served[name])
                fetched.add(name)

        added = False
        for name in list(reached):
            node = iri(name)
            # forward isMaterializedBy: name -> FDMO
            for o in knowledge.objects(node, IS_MATERIALIZED_BY):
                if not (isinstance(o, URIRef) and str(o).startswith(ns_base)):
                    continue
                tgt = local(o)
                if tgt not in served:
                    dangling.append((name, "isMaterializedBy", str(o)))
                elif tgt not in reached:
                    reached.add(tgt)
                    mechanism[tgt] = f"isMaterializedBy from {name}"
                    added = True
            # inverse isMetadataOf: FMR -> name
            for s in knowledge.subjects(IS_METADATA_OF, node):
                if not (isinstance(s, URIRef) and str(s).startswith(ns_base)):
                    continue
                src = local(s)
                if src not in served:
                    dangling.append((src, "isMetadataOf", name))
                elif src not in reached:
                    reached.add(src)
                    mechanism[src] = f"inverse isMetadataOf of {name}"
                    added = True

        if not added:
            break

    ok = reached >= set(served) and not dangling
    return ok, reached, served, mechanism, dangling


def discover(args: list[str]) -> list[Path]:
    if not args:
        return sorted(d for d in DEFAULT_ROOT.iterdir()
                      if d.is_dir() and (d / f"{d.name}.trig").is_file())
    out: list[Path] = []
    for a in args:
        p = Path(a)
        if not p.is_dir():
            print(f"  ! {a}: not a directory, skipping")
            continue
        out.append(p)
    return out


def main(argv: list[str]) -> int:
    fdos = discover(argv)
    if not fdos:
        raise SystemExit("No FDO output directories found. Run the pipeline first.")

    all_ok = True
    for fdo_dir in fdos:
        ok, reached, served, mechanism, dangling = check_roundtrip(fdo_dir)
        status = "PASS" if ok else "FAIL"
        print(f"\n{fdo_dir.name}: {status}  "
              f"({len(reached & set(served))}/{len(served)} objects reached)")
        for name in sorted(served):
            mark = "ok " if name in reached else "!! "
            via = mechanism.get(name, "UNREACHABLE")
            print(f"    {mark}{name:<34} <- {via}")
        for src, pred, tgt in dangling:
            print(f"    !! dangling: {src} --{pred}--> {tgt} (no served object)")
        all_ok &= ok
    print("\n" + "=" * 56)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
