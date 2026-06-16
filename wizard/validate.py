#!/usr/bin/env python3
"""Validate an FDO TriG file against the FDOF SHACL shapes.

Usage: python validate.py <file.trig> [<file.trig> ...]

Flattens each TriG dataset (default + named graphs) into one graph before
validating, since FDOF constraints span graphs (skeleton vs. FMR graphs).
Inference uses a schema-only view of FDOF-O/-T: their own NamedIndividual
self-descriptions are stripped so the data shapes don't apply to them.
"""
import sys
from rdflib import Dataset, Graph, Namespace, RDF, OWL, URIRef
from pyshacl import validate

FDOF = Namespace("https://w3id.org/fdof/ontology#")
SHAPES = "fdof-shapes.ttl"
ONTOLOGIES = ["fdof-o.ttl", "fdof-t.ttl"]

def schema_only(path: str) -> Graph:
    """Load an ontology as an inference (TBox) graph.

    Strip only the ontology IRI's self-description as an FDO instance
    (gupri/isMaterializedBy etc.), not the class terms - stripping those
    would drop their subClassOf axioms and break the inference chain.
    """
    g = Graph().parse(path, format="turtle")
    self_iris = {
        URIRef("https://w3id.org/fdof/ontology"),
        URIRef("https://w3id.org/fdof/types#"),
    }
    clean = Graph()
    for s, p, o in g:
        if s in self_iris and (
            (p == RDF.type and o in (OWL.NamedIndividual,
                                     FDOF.FAIRInformationObject,
                                     FDOF.FAIRMediaObject))
            or p in (FDOF.gupri, FDOF.isMaterializedBy,
                     FDOF.hasInformationObjectType)
        ):
            continue
        clean.add((s, p, o))
    return clean


def flatten(trig_path: str) -> Graph:
    ds = Dataset()
    ds.parse(trig_path, format="trig")
    union = Graph()
    for g in ds.graphs():
        for triple in g:
            union.add(triple)
    return union


def inference_graph() -> Graph:
    g = Graph()
    for path in ONTOLOGIES:
        g += schema_only(path)
    return g


def main(paths) -> int:
    shapes = Graph().parse(SHAPES, format="turtle")
    ont = inference_graph()
    overall_ok = True
    for trig_path in paths:
        data = flatten(trig_path)
        conforms, _, report = validate(
            data_graph=data, shacl_graph=shapes, ont_graph=ont,
            inference="rdfs", advanced=True,
        )
        viol = report.count("Severity: sh:Violation")
        warn = report.count("Severity: sh:Warning")
        print(f"{trig_path}: {len(data)} triples | "
              f"conforms={conforms} | violations={viol} warnings={warn}")
        if not conforms:
            for line in report.splitlines():
                s = line.strip()
                if s.startswith(("Focus Node:", "Message:")):
                    print("    " + s)
            overall_ok = False
    return 0 if overall_ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate.py <file.trig> [<file.trig> ...]")
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))
