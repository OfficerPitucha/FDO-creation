# FAIR Digital Object Creation Pipeline

A wizard-driven Python pipeline that takes an arbitrary input file (CSV, image, PDF,
etc) and produces a complete **FAIR Digital Object (FDO)** - a graph of
interlinked RDF artefacts conforming to the FAIR Digital Object Framework (FDOF). The
pipeline auto-detects what it can from the file, asks the user for the rest, validates
the result with SHACL, and writes both a bundled TriG file and a per-object directory
tree that can be served over HTTP.

This is a BSc thesis prototype.

## Requirements

- Python 3.11+
- Dependencies declared in [pyproject.toml](pyproject.toml)

```powershell
pip install .
```

## Usage
Taking timezone.csv as an example.

Run these from the `wizard/` directory (`cd wizard`). Output is always written to `/out/`

### CLI

Prompts for title, description, license, and information-object type, everything else
is auto-derived.

```powershell
python pipeline.py timezone.csv
```

### GUI

Launch empty, drag a file in:
```powershell
python pipeline.py --gui
# or
python gui.py
```

Launch pre-populated:
```powershell
python pipeline.py timezone.csv --gui
```

### Serve the result

The output directory can be served over HTTP to resolve the FDOF identifier endpoints:

```powershell
python serve.py --root ../out/timezone

# in another shell:
curl http://localhost:7070/timezone
curl http://localhost:7070/timezone/identifierRecord
curl http://localhost:7070/timezone/metadataRecord
curl http://localhost:7070/timezone/type
```

## Output

`<slug>` is derived from the input's name.
`<Suffix>` is derived from the input's MIME type.

Each object directory holds the four FDOF records: `digitalObject.*`,
`identifierRecord.trig`, `metadataRecord.trig`, and `type.ttl` (terminal metadata
records have no `metadataRecord.trig` by design).

```
out/<slug>/
    <slug>.trig                       # bundled TriG (canonical pipeline output)
    <slug>/                           # FDIO
    <slug><Suffix>/                   # input FDMO (raw bytes)
    <slug>Metadata/                   # FMR of the FDIO
    <slug>MetadataTrig/               # TriG FDMO (the bundled TriG)
    <slug><Suffix>Metadata/           # FMR of the input FDMO (terminal)
    <slug>MetadataTrigMetadata/       # FMR of the TriG FDMO (terminal)
    <slug>MetaMetadata/               # optional
```
