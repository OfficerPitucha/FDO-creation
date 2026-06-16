#!/usr/bin/env python3
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)
from rdflib import URIRef

from pipeline import (
    DEFAULT_OUT, IO_TYPES, LICENSES, Decisions, Defaults, Names, Result,
    compute_defaults, finalize,
)


# ---------------------------------------------------------------------
# Drag-and-drop file zone
# ---------------------------------------------------------------------

class DropZone(QFrame):
    """A frame that accepts a dragged file and emits its local path."""

    fileDropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setAcceptDrops(True)
        self.setMinimumHeight(80)
        self.setStyleSheet(
            "QFrame { border: 2px dashed #888; border-radius: 8px; }"
            "QLabel { border: none; color: #666; }"
        )
        lay = QVBoxLayout(self)
        self._label = QLabel("Drag an input file here  -  or  -  Browse…")
        self._label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._label)

    def set_path(self, path: str) -> None:
        self._label.setText(path)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                self.fileDropped.emit(local)
                break


# ---------------------------------------------------------------------
# Build worker - keeps SHACL/IO off the UI thread
# ---------------------------------------------------------------------

class BuildWorker(QThread):
    done = Signal(object)   # Result
    failed = Signal(str)

    def __init__(self, defaults: Defaults, decisions: Decisions,
                 base_out: Path, clean: bool) -> None:
        super().__init__()
        self._defaults = defaults
        self._decisions = decisions
        self._base_out = base_out
        self._clean = clean

    def run(self) -> None:
        try:
            result = finalize(self._defaults, self._decisions,
                              self._base_out, self._clean,
                              skip_validation=False)
            self.done.emit(result)
        except Exception:  # surface any pipeline error in the GUI
            self.failed.emit(traceback.format_exc())


# ---------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------

NAME_LABELS = {
    "fdio": "FDIO",
    "fdmo_input": "Input FDMO",
    "fdmo_trig": "TriG FDMO",
    "fmr": "FMR (of FDIO)",
    "fmr_meta": "MetaMetadata (FMR-of-FMR)",
    "fmr_input": "FMR of input FDMO",
    "fmr_trig": "FMR of TriG FDMO",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FDO Creation Wizard")
        self.resize(640, 800)
        self._defaults: Defaults | None = None
        self._worker: BuildWorker | None = None

        root = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(root)

        # --- Drop zone + Browse ---
        self.drop = DropZone()
        self.drop.fileDropped.connect(self.load_file)
        outer.addWidget(self.drop)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_input)
        outer.addWidget(browse)

        # --- Descriptive metadata (elicited) ---
        meta_box = QGroupBox("Descriptive metadata")
        meta = QFormLayout(meta_box)
        self.title = QLineEdit()
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(60)
        self.license = QComboBox()
        for label, iri in LICENSES:
            self.license.addItem(label, iri)
        self.iotype = QComboBox()
        for label, iri in IO_TYPES:
            self.iotype.addItem(label, iri)
        meta.addRow("Title", self.title)
        meta.addRow("Description", self.description)
        meta.addRow("License", self.license)
        meta.addRow("IO type", self.iotype)
        outer.addWidget(meta_box)

        # --- Auto-computed ---
        auto_box = QGroupBox("Auto-computed")
        auto = QFormLayout(auto_box)
        self.slug = QLineEdit()
        self.fdmo_suffix = QLineEdit()
        self.mime = QLineEdit()
        self.byte_size = QLineEdit()
        self.issued = QLineEdit()
        self.encoding_iri = QLineEdit()
        self.input_download_url = QLineEdit()
        self.trig_download_url = QLineEdit()
        self.mime_subtype_label = QLineEdit()
        auto.addRow("Slug", self.slug)
        auto.addRow("FDMO suffix", self.fdmo_suffix)
        auto.addRow("MIME", self.mime)
        auto.addRow("Byte size", self.byte_size)
        auto.addRow("Issued (date)", self.issued)
        auto.addRow("Encoding IRI", self.encoding_iri)
        auto.addRow("Input download URL", self.input_download_url)
        auto.addRow("TriG download URL", self.trig_download_url)
        auto.addRow("Media subtype label", self.mime_subtype_label)
        outer.addWidget(auto_box)

        # --- Artifact names ---
        names_box = QGroupBox("Artifact names")
        names_form = QFormLayout(names_box)
        self.name_edits: dict[str, QLineEdit] = {}
        for field in Names.FIELDS:
            edit = QLineEdit()
            self.name_edits[field] = edit
            names_form.addRow(NAME_LABELS[field], edit)
        outer.addWidget(names_box)

        # --- Options ---
        opt_box = QGroupBox("Options")
        opt = QFormLayout(opt_box)
        out_row = QHBoxLayout()
        self.out_dir = QLineEdit(str(DEFAULT_OUT))
        out_browse = QPushButton("…")
        out_browse.setFixedWidth(32)
        out_browse.clicked.connect(self._browse_out_dir)
        out_row.addWidget(self.out_dir)
        out_row.addWidget(out_browse)
        out_container = QWidget()
        out_container.setLayout(out_row)
        opt.addRow("Output dir", out_container)
        self.clean = QCheckBox("Wipe output directory before writing")
        opt.addRow("", self.clean)
        self.metameta = QCheckBox("Create metadata of metadata")
        self.metameta.toggled.connect(self._on_metameta_toggled)
        opt.addRow("", self.metameta)
        outer.addWidget(opt_box)
        self._on_metameta_toggled(False)  # disable the fmr_meta name row

        # --- Build button + status ---
        self.build_btn = QPushButton("Build FDO")
        self.build_btn.clicked.connect(self._on_build)
        self.build_btn.setEnabled(False)
        outer.addWidget(self.build_btn)

        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setFixedHeight(140)
        self.status.setPlaceholderText("Status will appear here.")
        outer.addWidget(self.status)

    # -----------------------------------------------------------------
    # File loading / population
    # -----------------------------------------------------------------

    def _browse_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select input file")
        if path:
            self.load_file(path)

    def _browse_out_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select output directory")
        if path:
            self.out_dir.setText(path)

    def load_file(self, path_str: str) -> None:
        path = Path(path_str)
        if not path.is_file():
            QMessageBox.warning(self, "Invalid file",
                                f"Not a file: {path}")
            return
        try:
            d = compute_defaults(path, self.metameta.isChecked())
        except Exception as exc:
            QMessageBox.critical(self, "Detection failed", str(exc))
            return
        self._defaults = d
        self.drop.set_path(str(path))
        self._populate(d)
        self.build_btn.setEnabled(True)

    def _populate(self, d: Defaults) -> None:
        self.title.setText(d.title_default)
        self.description.setPlainText("")
        self.license.setCurrentIndex(0)
        self.iotype.setCurrentIndex(d.default_io_index)
        self.slug.setText(d.slug)
        self.fdmo_suffix.setText(d.fdmo_suffix)
        self.mime.setText(d.mime)
        self.byte_size.setText(str(d.byte_size))
        self.issued.setText(d.issued)
        self.encoding_iri.setText(str(d.input_encoding_iri))
        self.input_download_url.setText(d.input_download_url)
        self.trig_download_url.setText(d.trig_download_url)
        self.mime_subtype_label.setText(d.mime_subtype_label)
        for field in Names.FIELDS:
            self.name_edits[field].setText(getattr(d.names, field))

    def _on_metameta_toggled(self, checked: bool) -> None:
        self.name_edits["fmr_meta"].setEnabled(checked)

    # -----------------------------------------------------------------
    # Build
    # -----------------------------------------------------------------

    def _collect(self) -> Decisions | None:
        try:
            byte_size = int(self.byte_size.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid value",
                                "Byte size must be an integer.")
            return None
        names = Names(**{f: self.name_edits[f].text().strip()
                         for f in Names.FIELDS})
        return Decisions(
            title=self.title.text().strip(),
            description=self.description.toPlainText().strip(),
            license_iri=str(self.license.currentData()),
            license_label=self.license.currentText(),
            iotype_iri=URIRef(str(self.iotype.currentData())),
            iotype_label=self.iotype.currentText(),
            slug=self.slug.text().strip(),
            fdmo_suffix=self.fdmo_suffix.text().strip(),
            mime=self.mime.text().strip(),
            issued=self.issued.text().strip(),
            byte_size=byte_size,
            input_encoding_iri=self.encoding_iri.text().strip(),
            input_download_url=self.input_download_url.text().strip(),
            trig_download_url=self.trig_download_url.text().strip(),
            mime_subtype_label=self.mime_subtype_label.text().strip(),
            names=names,
            create_metametadata=self.metameta.isChecked(),
        )

    def _on_build(self) -> None:
        if self._defaults is None:
            return
        decisions = self._collect()
        if decisions is None:
            return
        self.build_btn.setEnabled(False)
        self.status.setPlainText("Building…")
        self._worker = BuildWorker(
            self._defaults, decisions,
            Path(self.out_dir.text().strip()), self.clean.isChecked(),
        )
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done(self, result: Result) -> None:
        self.build_btn.setEnabled(True)
        if not result.conforms:
            lines = [
                f"SHACL validation FAILED - "
                f"{result.violations} violation(s), {result.warnings} warning(s).",
                "Fix the fields below and Build again.",
                "",
            ]
            for line in result.report.splitlines():
                s = line.strip()
                if s.startswith(("Focus Node:", "Message:")):
                    lines.append(f"  {s}")
            self.status.setPlainText("\n".join(lines))
            return
        objects = len(result.split_summary)
        self.status.setPlainText(
            f"Done - conforms, {objects} object(s), "
            f"{result.warnings} warning(s), "
            f"type fallbacks: {result.fallback_count}.\n"
            f"Output: {result.out_dir}\n"
            f"Wizard log: {result.log_path}\n\n"
            f"Serve with:\n"
            f"  serve.py --root \"{result.out_dir}\""
        )

    def _on_failed(self, tb: str) -> None:
        self.build_btn.setEnabled(True)
        self.status.setPlainText("Build error:\n" + tb)


def launch(initial_path: Path | None = None) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    if initial_path is not None:
        win.load_file(str(initial_path))
    app.exec()


if __name__ == "__main__":
    arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    launch(arg)
