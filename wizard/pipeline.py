#!/usr/bin/env python3
"""
Steps:
  1. Detect MIME type from extension or content-sniffing.
  2. Mint slug + GUPRIs from the input filename. FDMO suffix derived from the MIME type.
  3. Map MIME -> FDOF-T classes via TYPE_REGISTRY, with supertype fallback (fdof:FAIRMediaObject / fdoft:Metadata) for unknown types.
  4. Wizard: title, description, license, IO type. The IO-type menu's default is set from the registry's FDIO suggestion for this MIME.
  5. Build the bundled TriG dataset.
  6. Run SHACL validation against fdof-shapes.ttl.
  7. Split into the per-FDO directory tree via split().
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pyshacl import validate as shacl_validate
from rdflib import Dataset, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD
import magic

from split_for_server import (
    FDOF, FDOFT, IANA_TRIG, split,
)
from validate import schema_only


HERE = Path(__file__).parent
DEFAULT_OUT = HERE.parent / "out"
SHAPES_PATH = HERE / "fdof-shapes.ttl"
ONTOLOGY_PATHS = [HERE / "fdof-o.ttl", HERE / "fdof-t.ttl"]

DCT = Namespace("http://purl.org/dc/terms/")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
FDOF_NS = Namespace(FDOF)
FDOFT_NS = Namespace(FDOFT)

LICENSES: list[tuple[str, str]] = [
    ("CC0 1.0 (public domain dedication)",
     "https://creativecommons.org/publicdomain/zero/1.0/"),
    ("CC BY 4.0",
     "https://creativecommons.org/licenses/by/4.0/"),
    ("CC BY-SA 4.0",
     "https://creativecommons.org/licenses/by-sa/4.0/"),
    ("MIT",
     "https://opensource.org/licenses/MIT"),
    ("Public Domain Mark 1.0",
     "https://creativecommons.org/publicdomain/mark/1.0/"),
]

IO_TYPES: list[tuple[str, URIRef]] = [
    ("Dataset",  FDOFT_NS.Dataset),
    ("Image",    FDOFT_NS.Photograph),
    ("Document", FDOFT_NS.Article),
    ("Generic",  FDOFT_NS.Metadata),
]


# ---------------------------------------------------------------------
# MIME to FDOF-T type registry
# ---------------------------------------------------------------------
# Each entry maps a MIME type to (FDMO class, default FDIO class).
# None means "no specific FDOF-T class - use supertype fallback."
# Adding rows here is the supported way to extend coverage

TYPE_REGISTRY: dict[str, tuple[URIRef | None, URIRef | None]] = {
    "text/csv":             (FDOFT_NS.Csv,    FDOFT_NS.Dataset),
    "text/turtle":          (FDOFT_NS.Ttl,    None),
    "text/plain":           (None,            None),
    "text/markdown":        (None,            None),
    "application/pdf":      (FDOFT_NS.Pdf,    FDOFT_NS.Article),
    "application/trig":     (FDOFT_NS.Trig,   None),
    "application/ld+json":  (FDOFT_NS.JsonLd, None),
    "image/jpeg":           (None,            FDOFT_NS.Photograph),
    "image/png":            (None,            FDOFT_NS.Photograph),
}

FDMO_SUPERTYPE = FDOF_NS.FAIRMediaObject  # FDMO fallback
FDIO_SUPERTYPE = FDOFT_NS.Metadata        # FDIO fallback

# Private instance resolves from Python's built-in table, not the
# machine-dependent global (Windows registry maps .csv to ms-excel).
_MIME = mimetypes.MimeTypes()
# RDF types absent from the stdlib table on every platform.
_MIME.add_type("text/turtle", ".ttl")
_MIME.add_type("application/ld+json", ".jsonld")


# ---------------------------------------------------------------------
# Wizard log
# ---------------------------------------------------------------------
#   "derived"    - computed from the input file or the fixed vocabulary.
#   "suggested"  - an automated default exists, but it is a semantic guess
#                  the user may need to correct (e.g. a filename-derived
#                  title, or the registry's information-object-type guess).
#   "user_only"  - no automated source can produce a correct value.
#   "structural" - a build-time toggle with a fixed default, not a
#                  metadata value.
CATEGORIES = ("derived", "suggested", "user_only", "structural")
_HAS_SOURCE = {"derived", "suggested"}  # decisions with an automated source


@dataclass
class WizardLog:
    entries: list[dict[str, Any]]

    @classmethod
    def new(cls) -> "WizardLog":
        return cls(entries=[])

    def record(self, field: str, category: str, *, value: Any,
               source: str = "") -> None:
        """Record one decision and its provenance category. `value` is the
        value actually used; the category answers RQ2 for that decision."""
        self.entries.append({
            "field": field,
            "category": category,
            "has_automated_source": category in _HAS_SOURCE,
            "value": str(value),
            "source": source,
        })

    def metric(self, field: str, value: Any, source: str) -> None:
        """Record a measurement (not a decision); excluded from the rates."""
        self.entries.append({
            "field": field, "category": "metric",
            "value": str(value), "source": source,
        })

    def save(self, path: Path) -> None:
        decisions = [e for e in self.entries if e["category"] != "metric"]
        by_cat = {c: sum(1 for e in decisions if e["category"] == c)
                  for c in CATEGORIES}
        n = len(decisions)

        def pct(k: int) -> float:
            return round(100 * k / n, 1) if n else 0.0

        # the lower bound counts only "derived" decisions as automated; the upper bound also 
        # counts the semantic "suggested" guesses. Only "user_only" decisions must be elicited.
        path.write_text(json.dumps({
            "summary": {
                "decisions": n,
                "by_category": by_cat,
                "must_elicit": by_cat["user_only"],
                "automation_rate_lower_pct": pct(by_cat["derived"]),
                "automation_rate_upper_pct": pct(
                    by_cat["derived"] + by_cat["suggested"]),
            },
            "entries": self.entries,
        }, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------

def prompt_text(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    ans = input(f"{question}{suffix}: ").strip()
    return ans or default


def prompt_choice(question: str, choices: list[tuple[str, Any]],
                   default_index: int = 0) -> tuple[str, Any]:
    print(question)
    for i, (label, _) in enumerate(choices):
        marker = " (default)" if i == default_index else ""
        print(f"  [{i}] {label}{marker}")
    while True:
        ans = input(f"Choice 0-{len(choices) - 1} "
                    f"(default {default_index}): ").strip()
        if ans == "":
            return choices[default_index]
        try:
            idx = int(ans)
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print("Invalid choice, try again.")


# ---------------------------------------------------------------------
# File-type detection + slug minting + MIME -> FDOF-T mapping
# ---------------------------------------------------------------------

def detect_input(path: Path) -> tuple[str, str]:
    """Detect (mimetype, detection_source). Always returns a usable MIME.

    Detection order:
      1. `mimetypes.guess_type` (extension-based, stdlib).
      2. `python-magic` content sniffing (for files with no/unknown ext).
      3. `application/octet-stream` ultimate fallback.
    """
    if not path.is_file():
        raise SystemExit(f"Input file not found: {path}")
    mime, _ = _MIME.guess_type(str(path))
    if mime:
        return mime, "mimetypes built-in table (extension)"
    try:
        sniffed = magic.from_file(str(path), mime=True)
    except Exception:  # keep the "always returns a usable MIME" guarantee
        sniffed = None
    if sniffed:
        return sniffed, "python-magic (content sniff)"
    return "application/octet-stream", "fallback (no detection)"


def map_types(mime: str) -> tuple[URIRef, URIRef, bool, bool]:
    """Look up FDMO / default-FDIO classes for a MIME type.

    Returns (fdmo_class, fdio_default_class, fdmo_exact, fdio_exact).
    The boolean flags say whether the registry had a specific class
    (True) or we had to fall back to the supertype (False), feeding the
    vocabulary-coverage counts.
    """
    fdmo_cls, fdio_cls = TYPE_REGISTRY.get(mime, (None, None))
    fdmo_exact = fdmo_cls is not None
    fdio_exact = fdio_cls is not None
    return (
        fdmo_cls if fdmo_exact else FDMO_SUPERTYPE,
        fdio_cls if fdio_exact else FDIO_SUPERTYPE,
        fdmo_exact,
        fdio_exact,
    )


def fdmo_suffix_for(mime: str) -> str:
    """TitleCase suffix appended to the slug for the input FDMO.

    Examples: text/csv -> Csv; application/pdf -> Pdf;
    image/jpeg -> Jpeg; application/ld+json -> JsonLd;
    application/octet-stream -> Bytes; audio/flac -> Flac.
    """
    if mime == "application/octet-stream" or "/" not in mime:
        return "Bytes"
    _, subtype = mime.split("/", 1)
    subtype = subtype.removeprefix("vnd.").removeprefix("x-")
    # "ld+json" -> "JsonLd"; "svg+xml" -> "XmlSvg"
    if "+" in subtype:
        primary, fmt = subtype.split("+", 1)
        primary_part = "".join(
            p.title() for p in primary.replace("-", " ").replace(".", " ").split()
        )
        return fmt.title() + primary_part
    parts = subtype.replace("-", " ").replace(".", " ").replace("_", " ").split()
    return "".join(p.title() for p in parts) or "Bytes"


def iana_uri_for(mime: str) -> URIRef:
    return URIRef(f"https://www.iana.org/assignments/media-types/{mime}")


def default_io_type_index(fdio_default: URIRef) -> int:
    """Pick the menu index whose IRI matches the registry's FDIO default."""
    for i, (_, iri) in enumerate(IO_TYPES):
        if iri == fdio_default:
            return i
    return 3  # Generic


def sluggify(stem: str) -> str:
    out: list[str] = []
    for ch in stem:
        if ch.isalnum():
            out.append(ch)
        elif ch in "-_ ":
            out.append("-")
    slug = "".join(out).strip("-").lower()
    return slug or "fdo"


# ---------------------------------------------------------------------
# Artifact names
# ---------------------------------------------------------------------

@dataclass
class Names:
    fdio: str
    fdmo_input: str
    fdmo_trig: str
    fmr: str
    fmr_meta: str
    fmr_input: str
    fmr_trig: str

    FIELDS = ("fdio", "fdmo_input", "fdmo_trig", "fmr",
              "fmr_meta", "fmr_input", "fmr_trig")


def derive_names(slug: str, fdmo_suffix: str) -> Names:
    """Single source of truth for the default object names."""
    return Names(
        fdio=slug,
        fdmo_input=slug + fdmo_suffix,
        fdmo_trig=slug + "MetadataTrig",
        fmr=slug + "Metadata",
        fmr_meta=slug + "MetaMetadata",
        fmr_input=slug + fdmo_suffix + "Metadata",
        fmr_trig=slug + "MetadataTrigMetadata",
    )


# ---------------------------------------------------------------------
# Defaults / Decisions / Result - the compute / collect / finalize split
# ---------------------------------------------------------------------

@dataclass
class Defaults:
    """Everything derivable from the input file alone - no user input,
    no logging. Pre-fills the wizard (CLI prompts / GUI form)."""
    input_path: Path
    mime: str
    mime_source: str
    fdmo_cls: URIRef
    fdio_default: URIRef
    fdmo_exact: bool
    fdio_exact: bool
    slug: str
    fdmo_suffix: str
    byte_size: int
    issued: str
    input_encoding_iri: URIRef
    input_download_url: str
    trig_download_url: str
    title_default: str
    default_io_index: int
    mime_subtype_label: str
    names: Names
    create_metametadata: bool = False


@dataclass
class Decisions:
    """The final, possibly-overridden values that drive graph construction.
    The CLI fills the auto fields straight from Defaults; the GUI may edit
    any of them."""
    title: str
    description: str
    license_iri: str
    license_label: str
    iotype_iri: URIRef
    iotype_label: str
    slug: str
    fdmo_suffix: str
    mime: str
    issued: str
    byte_size: int
    input_encoding_iri: str
    input_download_url: str
    trig_download_url: str
    mime_subtype_label: str
    names: Names
    create_metametadata: bool


@dataclass
class Result:
    """Outcome of finalize(), so the GUI can report without the pipeline
    calling raise SystemExit mid-build."""
    conforms: bool
    violations: int
    warnings: int
    report: str
    out_dir: Path
    bundled_trig_path: Path
    log_path: Path | None
    split_summary: list[tuple[str, str, str]]
    fallback_count: int


def compute_defaults(input_path: Path,
                     create_metametadata: bool = False) -> Defaults:
    """Run the automated detection/derivation steps (no logging)."""
    mime, mime_source = detect_input(input_path)
    fdmo_cls, fdio_default, fdmo_exact, fdio_exact = map_types(mime)
    slug = sluggify(input_path.stem)
    fdmo_suffix = fdmo_suffix_for(mime)
    byte_size = input_path.stat().st_size
    issued = date.today().isoformat()
    input_encoding_iri = iana_uri_for(mime)
    input_ext = input_path.suffix.lstrip(".").lower() or "bin"
    input_download_url = f"https://example.org/fdo/{slug}/{slug}.{input_ext}"
    trig_download_url = f"https://example.org/fdo/{slug}/{slug}Metadata.trig"
    title_default = (input_path.stem
                     .replace("_", " ").replace("-", " ").title())
    default_io_index = default_io_type_index(fdio_default)
    mime_subtype_label = (fdmo_suffix.upper()
                          if len(fdmo_suffix) <= 4 else fdmo_suffix)
    return Defaults(
        input_path=input_path,
        mime=mime, mime_source=mime_source,
        fdmo_cls=fdmo_cls, fdio_default=fdio_default,
        fdmo_exact=fdmo_exact, fdio_exact=fdio_exact,
        slug=slug, fdmo_suffix=fdmo_suffix,
        byte_size=byte_size, issued=issued,
        input_encoding_iri=input_encoding_iri,
        input_download_url=input_download_url,
        trig_download_url=trig_download_url,
        title_default=title_default,
        default_io_index=default_io_index,
        mime_subtype_label=mime_subtype_label,
        names=derive_names(slug, fdmo_suffix),
        create_metametadata=create_metametadata,
    )


def decisions_from_defaults(d: Defaults, *, title: str, description: str,
                            license_iri: str, license_label: str,
                            iotype_iri: URIRef, iotype_label: str) -> Decisions:
    """Build a Decisions object that accepts every auto value unchanged,
    overriding only the four elicited fields. Used by the CLI path."""
    return Decisions(
        title=title, description=description,
        license_iri=license_iri, license_label=license_label,
        iotype_iri=iotype_iri, iotype_label=iotype_label,
        slug=d.slug, fdmo_suffix=d.fdmo_suffix, mime=d.mime,
        issued=d.issued, byte_size=d.byte_size,
        input_encoding_iri=str(d.input_encoding_iri),
        input_download_url=d.input_download_url,
        trig_download_url=d.trig_download_url,
        mime_subtype_label=d.mime_subtype_label,
        names=d.names, create_metametadata=d.create_metametadata,
    )


# ---------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------

def build_dataset(
    slug: str,
    fdio_type_iri: URIRef,
    fdmo_suffix: str,
    input_encoding_iri: URIRef,
    title: str,
    description: str,
    license_iri: URIRef,
    issued_date: str,
    byte_size: int,
    input_download_url: str,
    trig_download_url: str,
    mime_subtype_label: str,
    names: Names | None = None,
    create_metametadata: bool = False,
) -> Dataset:
    """Construct the bundled TriG (skeleton + per-FMR metadata graphs)."""
    if names is None:
        names = derive_names(slug, fdmo_suffix)
    ns_base = f"https://example.org/fdo/{slug}#"
    NS = Namespace(ns_base)
    id_base = f"https://example.org/fdo/{slug}/"

    def gupri(local: str) -> Literal:
        return Literal(id_base + local, datatype=XSD.anyURI)

    ds = Dataset()
    ds.bind("fdof", FDOF)
    ds.bind("fdoft", FDOFT)
    ds.bind("dct", DCT)
    ds.bind("dcat", DCAT)
    ds.bind("xsd", XSD)
    ds.bind("", ns_base)

    fdio       = NS[names.fdio]
    fdmo_input = NS[names.fdmo_input]
    fdmo_trig  = NS[names.fdmo_trig]
    fmr        = NS[names.fmr]
    fmr_meta   = NS[names.fmr_meta]
    fmr_input  = NS[names.fmr_input]
    fmr_trig   = NS[names.fmr_trig]

    # -----------------------------------------------------------------
    # Skeleton graph
    # -----------------------------------------------------------------
    sk = ds.graph(NS.skeleton)

    sk.add((fdio, RDF.type, FDOF_NS.FAIRInformationObject))
    sk.add((fdio, FDOF_NS.hasInformationObjectType, fdio_type_iri))
    sk.add((fdio, FDOF_NS.gupri, gupri(names.fdio)))
    sk.add((fdio, FDOF_NS.isMaterializedBy, fdmo_input))

    sk.add((fmr, RDF.type, FDOF_NS.FAIRMetadataRecord))
    sk.add((fmr, FDOF_NS.hasInformationObjectType, FDOFT_NS.Metadata))
    sk.add((fmr, FDOF_NS.gupri, gupri(names.fmr)))
    sk.add((fmr, FDOF_NS.isMetadataOf, fdio))
    sk.add((fmr, FDOF_NS.isMaterializedBy, fdmo_trig))

    if create_metametadata:
        sk.add((fmr_meta, RDF.type, FDOF_NS.FAIRMetadataRecord))
        sk.add((fmr_meta, FDOF_NS.hasInformationObjectType,
                FDOFT_NS.MetaMetadata))
        sk.add((fmr_meta, FDOF_NS.gupri, gupri(names.fmr_meta)))
        sk.add((fmr_meta, FDOF_NS.isMetadataOf, fmr))
        sk.add((fmr_meta, FDOF_NS.isMaterializedBy, fdmo_trig))

    sk.add((fmr_input, RDF.type, FDOF_NS.FAIRMetadataRecord))
    sk.add((fmr_input, FDOF_NS.hasInformationObjectType,
            FDOFT_NS.MediaObjectMetadataRecord))
    sk.add((fmr_input, FDOF_NS.gupri, gupri(names.fmr_input)))
    sk.add((fmr_input, FDOF_NS.isMetadataOf, fdmo_input))
    sk.add((fmr_input, FDOF_NS.isMaterializedBy, fdmo_trig))

    sk.add((fmr_trig, RDF.type, FDOF_NS.FAIRMetadataRecord))
    sk.add((fmr_trig, FDOF_NS.hasInformationObjectType,
            FDOFT_NS.MediaObjectMetadataRecord))
    sk.add((fmr_trig, FDOF_NS.gupri, gupri(names.fmr_trig)))
    sk.add((fmr_trig, FDOF_NS.isMetadataOf, fdmo_trig))
    sk.add((fmr_trig, FDOF_NS.isMaterializedBy, fdmo_trig))

    sk.add((fdmo_input, RDF.type, FDOF_NS.FAIRMediaObject))
    sk.add((fdmo_input, FDOF_NS.hasEncodingFormat, input_encoding_iri))
    sk.add((fdmo_input, FDOF_NS.gupri, gupri(names.fdmo_input)))

    sk.add((fdmo_trig, RDF.type, FDOF_NS.FAIRMediaObject))
    sk.add((fdmo_trig, FDOF_NS.hasEncodingFormat, IANA_TRIG))
    sk.add((fdmo_trig, FDOF_NS.gupri, gupri(names.fdmo_trig)))

    # -----------------------------------------------------------------
    # FMR named graph: rich metadata about the FDIO
    # -----------------------------------------------------------------
    md = ds.graph(fmr)
    md.add((fmr, FDOF_NS.isMetadataOf, fdio))
    md.add((fdio, RDF.type, DCAT.Dataset))
    md.add((fdio, RDF.type, FDOF_NS.FAIRInformationObject))
    md.add((fdio, FDOF_NS.hasInformationObjectType, fdio_type_iri))
    md.add((fdio, FDOF_NS.gupri, gupri(names.fdio)))
    md.add((fdio, DCT.identifier, Literal(id_base + names.fdio)))
    md.add((fdio, FDOF_NS.isMaterializedBy, fdmo_input))
    md.add((fdio, DCAT.distribution, fdmo_input))
    md.add((fdio, DCT.title, Literal(title)))
    if description:
        md.add((fdio, DCT.description, Literal(description)))
    md.add((fdio, DCT.license, license_iri))
    md.add((fdio, DCT.issued, Literal(issued_date, datatype=XSD.date)))

    # -----------------------------------------------------------------
    # FMR-of-FMR graph (optional - only when create_metametadata is set)
    # -----------------------------------------------------------------
    if create_metametadata:
        mm = ds.graph(fmr_meta)
        mm.add((fmr_meta, FDOF_NS.isMetadataOf, fmr))
        mm.add((fmr, RDF.type, FDOF_NS.FAIRMetadataRecord))
        mm.add((fmr, FDOF_NS.hasInformationObjectType, FDOFT_NS.Metadata))
        mm.add((fmr, FDOF_NS.isMetadataOf, fdio))
        mm.add((fmr, FDOF_NS.gupri, gupri(names.fmr)))
        mm.add((fmr, DCT.identifier, Literal(id_base + names.fmr)))
        mm.add((fmr, FDOF_NS.isMaterializedBy, fdmo_trig))
        mm.add((fmr, DCAT.distribution, fdmo_trig))
        mm.add((fmr, DCT.title, Literal(f"Metadata Record of the {title}")))
        mm.add((fmr, DCT.license, license_iri))
        mm.add((fmr, DCT.issued, Literal(issued_date, datatype=XSD.date)))

    # -----------------------------------------------------------------
    # FMR of input-FDMO graph
    # -----------------------------------------------------------------
    cm = ds.graph(fmr_input)
    cm.add((fmr_input, FDOF_NS.isMetadataOf, fdmo_input))
    cm.add((fdmo_input, RDF.type, DCAT.Distribution))
    cm.add((fdmo_input, RDF.type, FDOF_NS.FAIRMediaObject))
    cm.add((fdmo_input, FDOF_NS.hasEncodingFormat, input_encoding_iri))
    cm.add((fdmo_input, DCAT.mediaType, input_encoding_iri))
    cm.add((fdmo_input, FDOF_NS.gupri, gupri(names.fdmo_input)))
    cm.add((fdmo_input, DCT.title, Literal(
        f"{mime_subtype_label} Distribution of the {title}", lang="en"
    )))
    cm.add((fdmo_input, DCAT.byteSize, Literal(byte_size, datatype=XSD.int)))
    cm.add((fdmo_input, DCAT.downloadURL, URIRef(input_download_url)))

    # -----------------------------------------------------------------
    # FMR-of-TriG-FDMO graph
    # -----------------------------------------------------------------
    tm = ds.graph(fmr_trig)
    tm.add((fmr_trig, FDOF_NS.isMetadataOf, fdmo_trig))
    tm.add((fdmo_trig, RDF.type, DCAT.Distribution))
    tm.add((fdmo_trig, RDF.type, FDOF_NS.FAIRMediaObject))
    tm.add((fdmo_trig, FDOF_NS.hasEncodingFormat, IANA_TRIG))
    tm.add((fdmo_trig, DCAT.mediaType, IANA_TRIG))
    tm.add((fdmo_trig, FDOF_NS.gupri, gupri(names.fdmo_trig)))
    tm.add((fdmo_trig, DCT.title, Literal(
        f"TriG Distribution of the Metadata Records Related to the {title} "
        f"FAIR Digital Object", lang="en"
    )))
    tm.add((fdmo_trig, DCAT.downloadURL, URIRef(trig_download_url)))

    return ds


# ---------------------------------------------------------------------
# SHACL validation
# ---------------------------------------------------------------------

def validate_dataset(ds: Dataset) -> tuple[bool, str]:
    """Same flatten + inference setup as wizard/validate.py."""
    shapes = Graph().parse(SHAPES_PATH, format="turtle")
    ont = Graph()
    for path in ONTOLOGY_PATHS:
        ont += schema_only(str(path))
    data = Graph()
    for g in ds.graphs():
        for t in g:
            data.add(t)
    conforms, _, report = shacl_validate(
        data_graph=data, shacl_graph=shapes, ont_graph=ont,
        inference="rdfs", advanced=True,
    )
    return conforms, report


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------

def collect_cli(defaults: Defaults) -> Decisions:
    """Run the four interactive prompts; accept every auto value unchanged."""
    d = defaults
    print(f"\n=== FDO Creation Wizard - {d.input_path.name} ===")
    print(f"Slug:               {d.slug}")
    print(f"Detected mimetype:  {d.mime}  ({d.mime_source})")
    fdmo_status = "exact" if d.fdmo_exact else "supertype fallback"
    fdio_status = "exact" if d.fdio_exact else "supertype fallback"
    print(f"FDMO class:         {d.fdmo_cls}  [{fdmo_status}]")
    print(f"FDIO class default: {d.fdio_default}  [{fdio_status}]")
    print(f"Byte size:          {d.byte_size}")
    print(f"Issued:             {d.issued}")
    print(f"MetaMetadata:       {'on' if d.create_metametadata else 'off'}\n")

    title = prompt_text("Title", default=d.title_default)
    description = prompt_text("Description", default="")
    license_label, license_iri_str = prompt_choice(
        "License", LICENSES, default_index=0,
    )
    iotype_label, iotype_iri = prompt_choice(
        "Information object type", IO_TYPES, default_index=d.default_io_index,
    )
    return decisions_from_defaults(
        d, title=title, description=description,
        license_iri=license_iri_str, license_label=license_label,
        iotype_iri=iotype_iri, iotype_label=iotype_label,
    )


def collect_noninteractive(defaults: Defaults) -> Decisions:
    """Accept every default value with --non-interactive"""
    d = defaults
    license_label, license_iri = LICENSES[0]
    iotype_label, iotype_iri = IO_TYPES[d.default_io_index]
    return decisions_from_defaults(
        d, title=d.title_default, description="",
        license_iri=license_iri, license_label=license_label,
        iotype_iri=iotype_iri, iotype_label=iotype_label,
    )


def _build_log(defaults: Defaults, dec: Decisions) -> WizardLog:
    """Classify every decision by provenance category (RQ2). The category
    is a property of the decision, not of the run; the logged value is the
    one actually used."""
    d, log = defaults, WizardLog.new()

    # derived - computed from the input file or the fixed vocabulary
    log.record("input.mimetype", "derived", value=dec.mime,
               source=d.mime_source)
    log.record("input.fdmo_class", "derived", value=d.fdmo_cls,
               source=f"registry {'exact' if d.fdmo_exact else 'supertype fallback'}")
    log.record("input.fdio_default_class", "derived", value=d.fdio_default,
               source=f"registry {'exact' if d.fdio_exact else 'supertype fallback'}")
    log.record("slug", "derived", value=dec.slug,
               source="sluggify(filename stem)")
    log.record("input.fdmo_suffix", "derived", value=dec.fdmo_suffix,
               source=f"fdmo_suffix_for({d.mime})")
    log.record("dcat:byteSize", "derived", value=dec.byte_size,
               source="os.stat().st_size")
    log.record("dct:issued", "derived", value=dec.issued,
               source="datetime.date.today()")
    log.record("fdof:hasEncodingFormat", "derived", value=dec.input_encoding_iri,
               source=f"IANA URI for {d.mime}")
    log.record("dcat:downloadURL[input]", "derived", value=dec.input_download_url,
               source="GUPRI base + extension")
    log.record("dcat:downloadURL[trig]", "derived", value=dec.trig_download_url,
               source="GUPRI base + Metadata.trig")
    for field in Names.FIELDS:
        if field == "fmr_meta" and not dec.create_metametadata:
            continue
        log.record(f"name.{field}", "derived", value=getattr(dec.names, field),
                   source="derive_names(slug, fdmo_suffix)")

    # suggested - an automated default exists, but it is a semantic guess
    log.record("dct:title", "suggested", value=dec.title,
               source="filename stem, title-cased (placeholder)")
    log.record("fdof:hasInformationObjectType", "suggested", value=dec.iotype_iri,
               source=f"registry FDIO suggestion for {d.mime} "
                      f"({'exact' if d.fdio_exact else 'supertype fallback'})")

    # user_only - no automated source can produce a correct value
    log.record("dct:description", "user_only", value=dec.description,
               source="no automated source")
    log.record("dct:license", "user_only", value=dec.license_iri,
               source="no automated source (menu default is an arbitrary constant)")

    # structural - a build-time toggle, not a metadata value
    log.record("fdo.create_metametadata", "structural",
               value=dec.create_metametadata,
               source="metadata-of-metadata toggle (off by default)")
    return log


def finalize(defaults: Defaults, dec: Decisions, base_out: Path,
             clean: bool, skip_validation: bool) -> Result:
    """Build, validate, split, and log. Returns a Result instead of
    raising on validation failure, so a GUI can report and re-edit."""
    log = _build_log(defaults, dec)

    out_dir = base_out / dec.slug
    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    encoding_iri = URIRef(dec.input_encoding_iri)
    ds = build_dataset(
        slug=dec.slug,
        fdio_type_iri=dec.iotype_iri,
        fdmo_suffix=dec.fdmo_suffix,
        input_encoding_iri=encoding_iri,
        title=dec.title,
        description=dec.description,
        license_iri=URIRef(dec.license_iri),
        issued_date=dec.issued,
        byte_size=dec.byte_size,
        input_download_url=dec.input_download_url,
        trig_download_url=dec.trig_download_url,
        mime_subtype_label=dec.mime_subtype_label,
        names=dec.names,
        create_metametadata=dec.create_metametadata,
    )
    bundled_trig_path = out_dir / f"{dec.slug}.trig"
    bundled_trig_path.write_bytes(ds.serialize(format="trig", encoding="utf-8"))

    conforms, report = True, ""
    viol = warn = 0
    if not skip_validation:
        conforms, report = validate_dataset(ds)
        viol = report.count("Severity: sh:Violation")
        warn = report.count("Severity: sh:Warning")
        if not conforms:
            return Result(
                conforms=False, violations=viol, warnings=warn,
                report=report, out_dir=out_dir,
                bundled_trig_path=bundled_trig_path, log_path=None,
                split_summary=[], fallback_count=0,
            )

    ns_base = f"https://example.org/fdo/{dec.slug}#"
    ont_graphs = [
        Graph().parse(HERE / "fdof-t.ttl", format="turtle"),
        Graph().parse(HERE / "fdof-o.ttl", format="turtle"),
    ]
    summary = split(
        ds=ds, ns_base=ns_base, ont_graphs=ont_graphs,
        out_dir=out_dir, raw_payloads={encoding_iri: defaults.input_path},
    )
    fallback_count = sum(
        1 for _, _, type_status in summary
        if type_status in ("supertype fallback", "stub")
    )
    log.metric("type_record.fallback_count", fallback_count,
               "count of objects whose /type fell back to FDOF-O supertype "
               "or a stub (vocabulary-coverage metric)")

    log_path = out_dir / "wizard-log.json"
    log.save(log_path)
    return Result(
        conforms=conforms, violations=viol, warnings=warn, report=report,
        out_dir=out_dir, bundled_trig_path=bundled_trig_path,
        log_path=log_path, split_summary=summary,
        fallback_count=fallback_count,
    )


def run(input_path: Path, base_out: Path, clean: bool,
        skip_validation: bool, create_metametadata: bool = False,
        non_interactive: bool = False) -> None:
    """CLI driver: compute defaults -> prompt -> finalize -> report."""
    defaults = compute_defaults(input_path, create_metametadata)
    if non_interactive:
        dec = collect_noninteractive(defaults)
    else:
        dec = collect_cli(defaults)

    print("\nBuilding bundled TriG...")
    result = finalize(defaults, dec, base_out, clean, skip_validation)
    print(f"  wrote {result.bundled_trig_path}")

    if not skip_validation:
        print("\nRunning SHACL validation...")
        print(f"  conforms={result.conforms} | "
              f"violations={result.violations} | warnings={result.warnings}")
        if not result.conforms:
            for line in result.report.splitlines():
                s = line.strip()
                if s.startswith(("Focus Node:", "Message:")):
                    print(f"    {s}")
            raise SystemExit(
                "Bundled TriG failed SHACL validation. "
                "Fix the wizard inputs or extend fdof-shapes.ttl."
            )

    print("\nSplitting into per-FDO directories...")
    for name, status, _ in result.split_summary:
        print(f"  {name:40s}  {status}")

    print(f"\nWizard log: {result.log_path}")
    print(f"Done. Serve with: serve.py --root \"{result.out_dir}\"")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="FDO Creation Pipeline (type-agnostic).")
    ap.add_argument("input", type=Path, nargs="?",
                    help="Path to the input file (any type). "
                         "Optional when --gui is given.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help="Output directory (default: ../out/).")
    ap.add_argument("--clean", action="store_true",
                    help="Wipe the output directory before writing.")
    ap.add_argument("--skip-validation", action="store_true",
                    help="Skip the SHACL validation step.")
    ap.add_argument("--with-metametadata", action="store_true",
                    help="Also build the FMR-of-FMR object.")
    ap.add_argument("--non-interactive", "-y", action="store_true",
                    help="Skip the wizard prompts and accept all defaults ")
    ap.add_argument("--gui", action="store_true",
                    help="Launch the PySide6 form instead of the CLI wizard.")
    args = ap.parse_args()

    if args.gui:
        from gui import launch
        launch(args.input)
        return

    if args.input is None:
        ap.error("input file is required unless --gui is given")
    run(args.input, args.out, args.clean, args.skip_validation,
        args.with_metametadata, non_interactive=args.non_interactive)


if __name__ == "__main__":
    main()
