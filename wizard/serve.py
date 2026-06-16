"""FDOF resolution server.

Serves a directory laid out as:
  <root>/{id}/digitalObject.<ext>
  <root>/{id}/identifierRecord.<ext>
  <root>/{id}/metadataRecord.<ext>
  <root>/{id}/type.<ext>

URL routing:
  GET /{id}                    -> {id}/digitalObject.*
  GET /{id}/identifierRecord   -> {id}/identifierRecord.*
  GET /{id}/metadataRecord     -> {id}/metadataRecord.*
  GET /{id}/type               -> {id}/type.*

Content-Type is derived from the on-disk extension.
"""

from __future__ import annotations

import argparse
import http.server
import socketserver
from pathlib import Path


MIME = {
    "csv":  "text/csv",
    "trig": "application/trig",
    "ttl":  "text/turtle",
    "json": "application/json",
    "jsonld": "application/ld+json",
    "txt":  "text/plain",
    "pdf":  "application/pdf",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
}

SUFFIXES = {"identifierRecord", "metadataRecord", "type"}


def make_handler(root: Path):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parts = [p for p in self.path.lstrip("/").split("/") if p]
            if len(parts) == 1:
                obj_id, stem = parts[0], "digitalObject"
            elif len(parts) == 2 and parts[1] in SUFFIXES:
                obj_id, stem = parts
            else:
                self.send_error(404, "Not an FDOF endpoint")
                return

            obj_dir = root / obj_id
            if not obj_dir.is_dir():
                self.send_error(404, f"No FDO with id '{obj_id}'")
                return

            matches = sorted(obj_dir.glob(f"{stem}.*"))
            if not matches:
                self.send_error(404, f"'{stem}' not found for '{obj_id}'")
                return

            path = matches[0]
            ext = path.suffix.lstrip(".").lower()
            ctype = MIME.get(ext, "application/octet-stream")
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:
            print(f"[{self.address_string()}] {fmt % args}")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).parent.parent / "out")
    ap.add_argument("--port", type=int, default=7070)
    args = ap.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(
            f"Root '{root}' does not exist. Run the pipeline first."
        )

    handler = make_handler(root)
    with socketserver.ThreadingTCPServer(("", args.port), handler) as httpd:
        print(f"Serving {root} on http://localhost:{args.port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")


if __name__ == "__main__":
    main()
