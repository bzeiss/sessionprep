"""ColorsPage — editable color palette for track groups."""

from __future__ import annotations

import copy
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .param_form import _argb_to_qcolor
from ..widgets import ColorGridPanel


class ColorsPage(QWidget):
    """Editable color palette (name + ARGB swatch per row).

    Implements the standard page interface:
        load(config)   — populate from config["colors"]
        commit(config) — write back to config["colors"]

    Also exposes color_provider() for GroupsPage to reference live data.
    """

    colorsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    # ── Page interface ────────────────────────────────────────────────

    def load(self, config: dict) -> None:
        from ..theme import PT_DEFAULT_COLORS
        colors = config.get("colors", [])
        if not colors:
            colors = copy.deepcopy(PT_DEFAULT_COLORS)
        self._table.blockSignals(True)
        self._table.setRowCount(len(colors))
        for row, entry in enumerate(colors):
            self._set_color_row(
                row, entry.get("name", ""), entry.get("argb", "#ff888888"))
        self._table.blockSignals(False)
        self._refresh_preview()

    def commit(self, config: dict) -> None:
        config["colors"] = self._read_all_colors()

    # ── Color provider (for GroupsPage) ───────────────────────────────

    def color_provider(self) -> tuple[list[str], Callable[[str], str | None]]:
        """Return (color_names, argb_lookup) from the current table state."""
        return self._color_names(), self._color_argb_for_name

    def all_colors_provider(self) -> list[dict[str, str]]:
        """Return full color list including empty-name entries."""
        return self._read_all_colors()

    # ── UI setup ─────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        desc = QLabel(
            "Color palette used for track groups. "
            "Double-click a swatch to edit."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(desc)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["#", "Name", "Color"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        ch = self._table.horizontalHeader()
        ch.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        ch.setSectionResizeMode(0, QHeaderView.Fixed)
        ch.resizeSection(0, 36)
        ch.setSectionResizeMode(1, QHeaderView.Stretch)
        ch.setSectionResizeMode(2, QHeaderView.Fixed)
        ch.resizeSection(2, 60)
        self._table.cellDoubleClicked.connect(self._on_swatch_dbl_click)
        self._table.keyPressEvent = self._table_key_press
        layout.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._on_remove)
        btn_row.addWidget(remove_btn)
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Color grid preview
        preview_label = QLabel("Palette Preview")
        preview_label.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(preview_label)
        self._grid_preview = ColorGridPanel(cell_height=22, parent=self)
        self._grid_preview.colorClicked.connect(self._on_preview_clicked)
        layout.addWidget(self._grid_preview)

        # Refresh preview when table contents change (name edits, etc.)
        self._table.cellChanged.connect(
            lambda _row, _col: self._refresh_preview())

    # ── Row helpers ───────────────────────────────────────────────────

    def _set_color_row(self, row: int, name: str, argb: str) -> None:
        idx_item = QTableWidgetItem(str(row + 1))
        idx_item.setFlags(idx_item.flags() & ~Qt.ItemIsEditable)
        idx_item.setForeground(QColor("#888888"))
        self._table.setItem(row, 0, idx_item)

        self._table.setItem(row, 1, QTableWidgetItem(name))

        swatch_item = QTableWidgetItem()
        swatch_item.setFlags(swatch_item.flags() & ~Qt.ItemIsEditable)
        swatch_item.setBackground(_argb_to_qcolor(argb))
        swatch_item.setData(Qt.UserRole, argb)
        swatch_item.setToolTip(argb)
        self._table.setItem(row, 2, swatch_item)

    def _read_colors(self) -> list[dict[str, str]]:
        colors = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 1)
            swatch_item = self._table.item(row, 2)
            if not name_item or not swatch_item:
                continue
            name = name_item.text().strip()
            argb = swatch_item.data(Qt.UserRole) or "#ff888888"
            if name:
                colors.append({"name": name, "argb": argb})
        return colors

    def _read_all_colors(self) -> list[dict[str, str]]:
        """Read all color entries including those with empty names."""
        colors = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 1)
            swatch_item = self._table.item(row, 2)
            if not swatch_item:
                continue
            name = name_item.text().strip() if name_item else ""
            argb = swatch_item.data(Qt.UserRole) or "#ff888888"
            colors.append({"name": name, "argb": argb})
        return colors

    def _color_names(self) -> list[str]:
        names = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item:
                name = item.text().strip()
                if name:
                    names.append(name)
        return names

    def _color_argb_for_name(self, name: str) -> str | None:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item and item.text().strip() == name:
                swatch = self._table.item(row, 2)
                if swatch:
                    return swatch.data(Qt.UserRole)
        return None

    # ── Slot handlers ─────────────────────────────────────────────────

    def _on_swatch_dbl_click(self, row: int, col: int) -> None:
        if col != 2:
            return
        item = self._table.item(row, 2)
        if not item:
            return
        current = _argb_to_qcolor(item.data(Qt.UserRole) or "#ff888888")
        color = QColorDialog.getColor(
            current, self, "Select Color", QColorDialog.ShowAlphaChannel)
        if color.isValid():
            argb = f"#{color.alpha():02x}{color.red():02x}{color.green():02x}{color.blue():02x}"
            item.setBackground(color)
            item.setData(Qt.UserRole, argb)
            item.setToolTip(argb)
            self._refresh_preview()

    def _on_add(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._set_color_row(row, "New Color", "#ff888888")
        self._table.scrollToBottom()
        self._table.editItem(self._table.item(row, 1))
        self._refresh_preview()

    def _on_remove(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
            self._refresh_preview()

    def _on_reset(self) -> None:
        from ..theme import PT_DEFAULT_COLORS
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(PT_DEFAULT_COLORS))
        for row, entry in enumerate(PT_DEFAULT_COLORS):
            self._set_color_row(row, entry["name"], entry["argb"])
        self._table.blockSignals(False)
        self._refresh_preview()

    def _refresh_preview(self):
        """Rebuild the grid preview from the current table state."""
        self._grid_preview.set_colors(self._read_all_colors())
        self.colorsChanged.emit()

    def _on_preview_clicked(self, index: int):
        """Select and scroll to the table row at *index*."""
        if 0 <= index < self._table.rowCount():
            self._table.selectRow(index)
            item = self._table.item(index, 1)
            if item:
                self._table.scrollToItem(item)

    def _table_key_press(self, event):
        """Clear the name column when Delete is pressed on a selected row."""
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            row = self._table.currentRow()
            if row >= 0:
                item = self._table.item(row, 1)
                if item:
                    item.setText("")
                    self._refresh_preview()
                return
        QTableWidget.keyPressEvent(self._table, event)
