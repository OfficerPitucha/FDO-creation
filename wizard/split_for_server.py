"""Split a bundled FDO TriG dataset into the per-FDO directory layout
served by serve.py.

Layout written:
  <out>/{id}/digitalObject.<ext>
  <out>/{id}/identifierRecord.trig
  <out>/{id}/metadataRecord.trig
  <out>/{id}/type.ttl
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Iterable

from rdflib import Dataset, Graph, URIRef


HERE = Path(__file__).parent

FDOF = "https://w3id.org/fdof/ontology#"
FDOFT = "https://w3id.org/fdof/types#"
RDF_TYPE = URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
IS_METADATA_OF = URIRef(FDOF + "isMetadataOf")
HAS_IOTYPE = URIRef(FDOF + "hasInformationObjectType")
FAIR_MEDIA_OBJECT = URIRef(FDOF + "FAIRMediaObject")
HAS_ENCODING = URIRef(FDOF + "hasEncodingFormat")

IANA_CSV = URIRef("https://www.iana.org/assignments/media-types/text/csv")
IANA_TRIG = URIRef("https://www.iana.org/assignments/media-types/application/trig")


def _bind_common(g, ns_base: str) -> None:
    g.bind("fdof", FDOF)
    g.bind("fdoft", FDOFT)
    g.bind("dct", "http://purl.org/dc/terms/")
    g.bind("dcat", "http://www.w3.org/ns/dcat#")
    g.bind("", ns_base)


def _short(uri: URIRef, ns_base: str) -> str:
    s = str(uri)
    assert s.startswith(ns_base), f"unexpected URI {s}"
    return s[len(ns_base):]


def _write_identity_record(obj_dir: Path, obj: URIRef, skeleton: Graph,
                            ns_base: str) -> None:
    ds = Dataset()
    _bind_common(ds, ns_base)
    g = ds.graph(URIRef(ns_base + _short(obj, ns_base) + "IdRecord"))
    for s, p, o in skeleton.triples((obj, None, None)):
        g.add((s, p, o))
    (obj_dir / "identifierRecord.trig").write_bytes(
        ds.serialize(format="trig", encoding="utf-8")
    )


def _write_metadata_record(obj_dir: Path, obj: URIRef, ds_in: Dataset,
                            skeleton_iri: URIRef, ns_base: str) -> bool:
    for g in ds_in.graphs():
        if g.identifier == skeleton_iri:
            continue
        if (g.identifier, IS_METADATA_OF, obj) in g:
            out_ds = Dataset()
            _bind_common(out_ds, ns_base)
            og = out_ds.graph(g.identifier)
            for t in g:
                og.add(t)
            (obj_dir / "metadataRecord.trig").write_bytes(
                out_ds.serialize(format="trig", encoding="utf-8")
            )
            return True
    return False


def _write_digital_object(obj_dir: Path, obj: URIRef, skeleton: Graph,
                           ds_in: Dataset, skeleton_iri: URIRef,
                           ns_base: str, payloads: dict[URIRef, Path],
                           bundled_trig_bytes: bytes,
                           trig_encoding_iri: URIRef) -> None:
    types = set(skeleton.objects(obj, RDF_TYPE))
    if FAIR_MEDIA_OBJECT in types:
        encoding = next(skeleton.objects(obj, HAS_ENCODING), None)
        if encoding is None:
            raise RuntimeError(f"FDMO {obj} has no fdof:hasEncodingFormat")
        # Special-case: an FDMO whose encoding is application/trig is the
        # bundled TriG file itself - serialize the in-memory dataset.
        if encoding == trig_encoding_iri:
            (obj_dir / "digitalObject.trig").write_bytes(bundled_trig_bytes)
            return
        # Otherwise copy a raw payload registered by the caller.
        if encoding in payloads:
            src = payloads[encoding]
            ext = src.suffix.lstrip(".") or "bin"
            shutil.copyfile(src, obj_dir / f"digitalObject.{ext}")
            return
        raise RuntimeError(
            f"No payload registered for FDMO {obj} encoding {encoding}"
        )

    # FDIO/FMR: reuse the metadata-record graph as the representation.
    for g in ds_in.graphs():
        if g.identifier == skeleton_iri:
            continue
        if (g.identifier, IS_METADATA_OF, obj) in g:
            tmp = Graph()
            _bind_common(tmp, ns_base)
            for t in g:
                tmp.add(t)
            (obj_dir / "digitalObject.ttl").write_bytes(
                tmp.serialize(format="turtle", encoding="utf-8")
            )
            return

    # No FMR points at this object - fall back to the skeleton slice.
    tmp = Graph()
    _bind_common(tmp, ns_base)
    for t in skeleton.triples((obj, None, None)):
        tmp.add(t)
    (obj_dir / "digitalObject.ttl").write_bytes(
        tmp.serialize(format="turtle", encoding="utf-8")
    )


def _write_type_record(obj_dir: Path, obj: URIRef, skeleton: Graph,
                        ont_graphs: list[Graph]) -> str:
    """Resolve the type class for `obj` and slice its definition.

    Search strategy (used to support arbitrary input file types):
      1. fdof:hasInformationObjectType on the object (FDIO/FMR path).
      2. A class declaring fdof:hasEncodingFormat == enc (FDMO path).
      3. Name guess fdoft:<subtype.capitalize()> against any ontology.
      4. Last-resort: fdof:FAIRMediaObject supertype (for unknown FDMOs).

    Returns one of "exact" / "supertype fallback" / "stub" for logging.
    """
    type_uri = next(skeleton.objects(obj, HAS_IOTYPE), None)
    status = "exact"

    if type_uri is None:
        enc = next(skeleton.objects(obj, HAS_ENCODING), None)
        if enc is not None:
            for ont in ont_graphs:
                for cls in ont.subjects(URIRef(FDOF + "hasEncodingFormat"), enc):
                    type_uri = cls
                    break
                if type_uri is not None:
                    break
        if type_uri is None and enc is not None:
            mime_subtype = str(enc).rsplit("/", 1)[-1]
            cand = URIRef(FDOFT + mime_subtype.capitalize())
            for ont in ont_graphs:
                if (cand, RDF_TYPE, None) in ont:
                    type_uri = cand
                    break
        if type_uri is None and enc is not None:
            type_uri = FAIR_MEDIA_OBJECT
            status = "supertype fallback"

    if type_uri is None:
        raise RuntimeError(f"Could not resolve type for {obj}")

    tmp = Graph()
    tmp.bind("fdof", FDOF)
    tmp.bind("fdoft", FDOFT)
    tmp.bind("rdfs", "http://www.w3.org/2000/01/rdf-schema#")
    tmp.bind("dct", "http://purl.org/dc/terms/")
    tmp.bind("owl", "http://www.w3.org/2002/07/owl#")
    for ont in ont_graphs:
        for t in ont.triples((type_uri, None, None)):
            tmp.add(t)
    if len(tmp) == 0:
        # Truly unknown class - emit a minimal owl:Class stub so
        # /<id>/type returns a valid (if empty) RDF document.
        tmp.add((type_uri, RDF_TYPE,
                 URIRef("http://www.w3.org/2002/07/owl#Class")))
        status = "stub"

    (obj_dir / "type.ttl").write_bytes(
        tmp.serialize(format="turtle", encoding="utf-8")
    )
    return status


def split(ds: Dataset, ns_base: str, ont_graphs: list[Graph], out_dir: Path,
          raw_payloads: dict[URIRef, Path],
          trig_encoding_iri: URIRef = IANA_TRIG) -> list[tuple[str, str, str]]:
    """Split a bundled FDO Dataset into the per-FDO directory tree.

    Args:
        ds: in-memory bundled TriG (skeleton + per-FMR named graphs).
        ns_base: the namespace of the FDO's local identifiers,
            e.g. "https://example.org/fdo/timezone#".
        ont_graphs: ontology graphs searched in order for type
            definitions. Typically [fdof-t, fdof-o] so we fall back to
            the FDOF-O supertype when FDOF-T has no specific class.
        out_dir: destination directory; per-object subdirectories are
            created under it.
        raw_payloads: maps an encoding IRI to the path of the raw bit
            sequence on disk. The bundled TriG itself does NOT need to
            be registered here; it is taken from `ds`.
        trig_encoding_iri: encoding IRI that identifies the bundled TriG
            as an FDMO; defaults to the IANA application/trig URI.

    Returns:
        A list of (object_short_name, status_summary, type_status).
        `type_status` is one of "exact" / "supertype fallback" / "stub".
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    skeleton_iri = URIRef(ns_base + "skeleton")
    skeleton = ds.graph(skeleton_iri)
    if len(skeleton) == 0:
        raise RuntimeError(f"Skeleton graph {skeleton_iri} is empty")

    bundled_trig_bytes = ds.serialize(format="trig", encoding="utf-8")

    objects: Iterable[URIRef] = sorted(
        {s for s in skeleton.subjects()
         if isinstance(s, URIRef) and str(s).startswith(ns_base)},
        key=str,
    )

    summary: list[tuple[str, str, str]] = []
    for obj in objects:
        name = _short(obj, ns_base)
        obj_dir = out_dir / name
        obj_dir.mkdir(parents=True, exist_ok=True)
        _write_identity_record(obj_dir, obj, skeleton, ns_base)
        wrote_md = _write_metadata_record(
            obj_dir, obj, ds, skeleton_iri, ns_base
        )
        _write_digital_object(
            obj_dir, obj, skeleton, ds, skeleton_iri, ns_base,
            raw_payloads, bundled_trig_bytes, trig_encoding_iri,
        )
        try:
            type_status = _write_type_record(
                obj_dir, obj, skeleton, ont_graphs
            )
        except RuntimeError as e:
            type_status = f"NO TYPE ({e})"
        md_status = "md" if wrote_md else "no md"
        summary.append((
            name,
            f"id  {md_status:6s}  dobj  type={type_status}",
            type_status,
        ))
    return summary


def main() -> None:
    """CLI: split the hand-built timezone reference TriG into out/.
    The pipeline (pipeline.py) calls split() directly and does not use this entry point.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=HERE.parent / "out")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--trig", type=Path,
                    default=HERE / "timezone.trig")
    ap.add_argument("--csv", type=Path,
                    default=HERE / "timezone.csv")
    ap.add_argument("--ns-base", type=str,
                    default="https://example.org/fdo/timezone#")
    args = ap.parse_args()

    if args.clean and args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    ds = Dataset()
    ds.parse(args.trig, format="trig")

    fdoft_graph = Graph()
    fdoft_graph.parse(HERE / "fdof-t.ttl", format="turtle")
    fdofo_graph = Graph()
    fdofo_graph.parse(HERE / "fdof-o.ttl", format="turtle")

    summary = split(
        ds=ds,
        ns_base=args.ns_base,
        ont_graphs=[fdoft_graph, fdofo_graph],
        out_dir=args.out,
        raw_payloads={IANA_CSV: args.csv},
    )
    print(f"Splitting {len(summary)} objects into {args.out}")
    for name, status, _ in summary:
        print(f"  {name:40s}  {status}")


if __name__ == "__main__":
    main()
