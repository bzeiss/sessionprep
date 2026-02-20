"""Generic reusable preset-management toolbar widget.

No app-specific dependency.  Portable: copy preset_panel.py to any PySide6 project.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)


class NamedPresetPanel(QWidget):
    """Combo + Add / Duplicate / Rename / Delete toolbar for named presets.

    The panel owns the combo and manages button enable-states.
    All data management is delegated to the caller via signals.

    Signals
    -------
    preset_switching(old_name, new_name)
        Emitted when the user selects a different preset.  The caller
        should *save* old_name's data and *load* new_name's data.
    preset_added(name)
        A new empty preset was created.  Caller creates the data entry.
    preset_duplicated(source_name, new_name)
        Caller deep-copies source_name's data to new_name.
    preset_renamed(old_name, new_name)
        Caller renames the data key.
    preset_deleted(name)
        The preset was removed from the combo.  Panel has already switched
        to the first protected (fallback) name.  Caller removes data entry.
    """

    preset_switching  = Signal(str, str)
    preset_added      = Signal(str)
    preset_duplicated = Signal(str, str)
    preset_renamed    = Signal(str, str)
    preset_deleted    = Signal(str)

    def __init__(
        self,
        initial_names: list[str],
        *,
        label: str = "",
        protected: frozenset[str] = frozenset({"Default"}),
        parent=None,
    ):
        super().__init__(parent)
        self._protected = frozenset(protected)
        self._current: str = initial_names[0] if initial_names else ""
        self._init_ui(initial_names, label)

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def current_name(self) -> str:
        return self._combo.currentText()

    def set_current(self, name: str) -> None:
        """Select a preset programmatically without emitting preset_switching."""
        self._combo.blockSignals(True)
        idx = self._combo.findText(name)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
            self._current = name
        self._combo.blockSignals(False)
        self._update_buttons()

    def all_names(self) -> list[str]:
        return [self._combo.itemText(i) for i in range(self._combo.count())]

    def reset(self, names: list[str], *, current: str | None = None) -> None:
        """Repopulate the combo without emitting any signals."""
        self._combo.blockSignals(True)
        self._combo.clear()
        for name in names:
            self._combo.addItem(name)
        self._current = names[0] if names else ""
        self._combo.blockSignals(False)
        if current:
            self.set_current(current)
        else:
            self._update_buttons()

    # ── UI setup ─────────────────────────────────────────────────────────

    def _init_ui(self, initial_names: list[str], label: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        if label:
            layout.addWidget(QLabel(label))

        self._combo = QComboBox()
        self._combo.setMinimumWidth(160)
        for name in initial_names:
            self._combo.addItem(name)
        layout.addWidget(self._combo, 1)

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(36)
        add_btn.setToolTip("New preset")
        add_btn.clicked.connect(self._on_add)
        layout.addWidget(add_btn)

        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._on_duplicate)
        layout.addWidget(dup_btn)

        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename)
        layout.addWidget(self._rename_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        layout.addWidget(self._delete_btn)

        self._combo.currentTextChanged.connect(self._on_combo_changed)
        self._update_buttons()

    # ── Slot helpers ─────────────────────────────────────────────────────

    def _existing_names(self) -> set[str]:
        return {self._combo.itemText(i) for i in range(self._combo.count())}

    def _prompt_unique_name(self, title: str, prompt: str,
                            initial: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, prompt, text=initial)
        if not ok or not name.strip():
            return None
        name = name.strip()
        if name in self._existing_names():
            QMessageBox.warning(
                self, "Duplicate Name",
                f"A preset named \u201c{name}\u201d already exists.")
            return None
        return name

    def _on_combo_changed(self, new_name: str) -> None:
        old = self._current
        self._current = new_name
        self.preset_switching.emit(old, new_name)
        self._update_buttons()

    def _update_buttons(self) -> None:
        protected = self._current in self._protected
        self._rename_btn.setEnabled(not protected)
        self._delete_btn.setEnabled(not protected)

    def _on_add(self) -> None:
        name = self._prompt_unique_name("New Preset", "Preset name:")
        if not name:
            return
        self._combo.blockSignals(True)
        self._combo.addItem(name)
        self._combo.setCurrentText(name)
        self._combo.blockSignals(False)
        self._current = name
        self._update_buttons()
        self.preset_added.emit(name)

    def _on_duplicate(self) -> None:
        source = self._current
        name = self._prompt_unique_name(
            "Duplicate Preset", "New preset name:",
            initial=f"{source} Copy")
        if not name:
            return
        self._combo.blockSignals(True)
        self._combo.addItem(name)
        self._combo.setCurrentText(name)
        self._combo.blockSignals(False)
        self._current = name
        self._update_buttons()
        self.preset_duplicated.emit(source, name)

    def _on_rename(self) -> None:
        old = self._current
        if old in self._protected:
            return
        name = self._prompt_unique_name(
            "Rename Preset", "New name:", initial=old)
        if not name or name == old:
            return
        idx = self._combo.findText(old)
        self._combo.blockSignals(True)
        self._combo.setItemText(idx, name)
        self._combo.blockSignals(False)
        self._current = name
        self._update_buttons()
        self.preset_renamed.emit(old, name)

    def _on_delete(self) -> None:
        current = self._current
        if current in self._protected:
            return
        reply = QMessageBox.question(
            self, "Delete Preset",
            f"Delete the preset \u201c{current}\u201d?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        idx = self._combo.findText(current)
        self._combo.blockSignals(True)
        self._combo.removeItem(idx)
        fallback = next(
            (n for n in self._protected if self._combo.findText(n) >= 0),
            self._combo.itemText(0) if self._combo.count() else "",
        )
        self._combo.setCurrentText(fallback)
        self._combo.blockSignals(False)
        self._current = fallback
        self._update_buttons()
        self.preset_deleted.emit(current)
