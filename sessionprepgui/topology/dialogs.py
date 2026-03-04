"""Dialogs specifically for the Track Layout tab."""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QLineEdit, QDialogButtonBox, QSizePolicy
)
from PySide6.QtCore import Qt
from ..theme import COLORS

def add_output_tracks_dialog(parent, topology, colors=None) -> list[tuple[str, int]]:
    """
    Shows a dialog to add one or more output tracks.
    Returns list of (filename, channels) or [] if cancelled.
    """
    if colors is None:
        colors = COLORS

    dlg = QDialog(parent)
    dlg.setWindowTitle("Add Output Track(s)")
    dlg.setMinimumWidth(520)
    outer = QVBoxLayout(dlg)
    outer.setSpacing(8)
    outer.setContentsMargins(12, 12, 12, 10)

    # Inline row: Create [n] new [ch]-ch tracks  Name: [____]
    row = QHBoxLayout()
    row.setSpacing(6)

    lbl_style = f"color: {colors['dim']};"
    create_lbl = QLabel("Create")
    create_lbl.setStyleSheet(lbl_style)
    row.addWidget(create_lbl)

    count_spin = QSpinBox()
    count_spin.setRange(1, 99)
    count_spin.setValue(1)
    count_spin.setFixedWidth(52)
    row.addWidget(count_spin)

    new_lbl = QLabel("new")
    new_lbl.setStyleSheet(lbl_style)
    row.addWidget(new_lbl)

    ch_spin = QSpinBox()
    ch_spin.setRange(1, 64)
    ch_spin.setValue(2)
    ch_spin.setFixedWidth(52)
    row.addWidget(ch_spin)

    ch_lbl = QLabel("-ch track(s)")
    ch_lbl.setStyleSheet(lbl_style)
    row.addWidget(ch_lbl)

    row.addSpacing(12)

    name_lbl = QLabel("Name:")
    name_lbl.setStyleSheet(lbl_style)
    row.addWidget(name_lbl)

    name_edit = QLineEdit("new_track.wav")
    name_edit.selectAll()
    name_edit.setMinimumWidth(160)
    row.addWidget(name_edit, 1)

    outer.addLayout(row)

    # Live preview
    preview = QLabel()
    preview.setStyleSheet(
        f"color: {colors['dim']}; font-style: italic; font-size: 11px;"
        "padding: 2px 0;")
    preview.setWordWrap(True)
    outer.addWidget(preview)

    def _update_preview():
        stem_raw = name_edit.text().strip() or "new_track.wav"
        dot = stem_raw.rfind(".")
        if dot > 0:
            s, e = stem_raw[:dot], stem_raw[dot:]
        else:
            s, e = stem_raw, ".wav"
        n = count_spin.value()
        ch = ch_spin.value()
        if n == 1:
            preview.setText(f"\u2192 {s}{e}  ({ch} ch)")
        else:
            names = ", ".join(
                f"{s}_{i + 1}{e}" for i in range(min(n, 3)))
            if n > 3:
                names += f", \u2026 ({n} total)"
            preview.setText(f"\u2192 {names}  ({ch} ch each)")

    name_edit.textChanged.connect(lambda *_: _update_preview())
    count_spin.valueChanged.connect(lambda *_: _update_preview())
    ch_spin.valueChanged.connect(lambda *_: _update_preview())
    _update_preview()

    # Buttons
    buttons = QDialogButtonBox(
        QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    outer.addWidget(buttons)
    name_edit.setFocus()

    if dlg.exec() != QDialog.Accepted:
        return []

    base_name = name_edit.text().strip()
    if not base_name:
        return []

    # Split stem and extension
    dot = base_name.rfind(".")
    if dot > 0:
        stem, ext = base_name[:dot], base_name[dot:]
    else:
        stem, ext = base_name, ".wav"

    n_tracks = count_spin.value()
    n_channels = ch_spin.value()

    results = []
    for i in range(n_tracks):
        suffix = f"_{i + 1}" if n_tracks > 1 else ""
        results.append((f"{stem}{suffix}{ext}", n_channels))
    return results
