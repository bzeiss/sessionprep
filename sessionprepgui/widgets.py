"""Reusable Qt widgets for batch-edit workflows.

The two classes here — ``BatchEditTableWidget`` and ``BatchComboBox`` — provide
a generic *multi-select → Alt+Shift+combo → apply to all* pattern that works
for **any** QTableWidget with cell-widget dropdowns.  This replicates the
behaviour found in DAWs, where Shift-selecting multiple tracks and
Alt-clicking a control applies the change to all selected tracks.

How it works
------------
``BatchEditTableWidget`` overrides ``selectionCommand()`` to prevent Qt from
clearing a multi-row selection when a persistent-editor cell widget (e.g. a
combo box) receives focus.  Without the override, ``checkPersistentEditorFocus``
calls ``setCurrentIndex`` which triggers ``ClearAndSelect`` — destroying the
selection the user just made.

Usage
-----
1. Inherit your table from ``BatchEditTableWidget`` instead of ``QTableWidget``.
2. Use ``BatchComboBox`` for cell-widget dropdowns.
3. Connect combo signals to ``textActivated`` (not ``currentTextChanged``) so
   that re-selecting the same value still fires the slot — important for
   applying the current value to the rest of the batch.
4. In your changed-slot::

       if combo.batch_mode:
           combo.batch_mode = False
           keys = table.batch_selected_keys()
           # … apply to each key …
           table.restore_selection(keys)

No app-specific dependencies.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QItemSelectionModel, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QProgressBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import COLORS


_SELECTION_COLOR = QColor(42, 109, 181, 160)  # semi-transparent blue


class ProgressPanel(QWidget):
    """Hidden-by-default status label + progress bar panel.

    Used as a bottom strip below content areas for async operations
    (Transfer, Prepare, etc.).  Callers interact via the public API;
    internal widgets are never accessed directly.
    """

    AUTO_HIDE_MS = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(3)
        self._label = QLabel("")
        self._label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 9pt;")
        layout.addWidget(self._label)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(14)
        layout.addWidget(self._bar)
        self.setVisible(False)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)

    def start(self, text: str = "Preparing\u2026"):
        """Reset bar to 0 and show the panel with *text*."""
        self._hide_timer.stop()
        self._bar.setValue(0)
        self._label.setText(text)
        self.setVisible(True)

    def set_message(self, text: str):
        """Update the status label text."""
        self._label.setText(text)

    def set_progress(self, current: int, total: int):
        """Update the progress bar value."""
        self._bar.setMaximum(max(total, 1))
        self._bar.setValue(current)

    def finish(self, text: str, auto_hide: bool = True):
        """Mark operation as complete: fill the bar, show *text*, auto-hide."""
        self._label.setText(text)
        self._bar.setValue(self._bar.maximum())
        if auto_hide:
            self._hide_timer.start(self.AUTO_HIDE_MS)

    def fail(self, text: str, auto_hide: bool = True):
        """Show failure message and optionally auto-hide."""
        self._label.setText(f"Failed: {text}")
        if auto_hide:
            self._hide_timer.start(self.AUTO_HIDE_MS)

    def _auto_hide(self):
        self.setVisible(False)


class _RowTintDelegate(QStyledItemDelegate):
    """Item delegate that blends selection highlight with row background.

    Qt's style engine uses ``QPalette::Highlight`` when
    ``State_Selected`` is set, ignoring ``backgroundBrush``.
    We therefore *remove* ``State_Selected`` and feed the correct
    pre-blended colour via ``backgroundBrush`` instead.
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        bg = index.data(Qt.BackgroundRole)
        has_bg = (bg is not None and isinstance(bg, QBrush)
                  and bg.style() != Qt.NoBrush)
        selected = bool(option.state & QStyle.State_Selected)

        if selected:
            option.state &= ~QStyle.State_Selected
            if has_bg:
                gc = bg.color()
                sc = _SELECTION_COLOR
                a = sc.alphaF()
                option.backgroundBrush = QBrush(QColor(
                    int(sc.red() * a + gc.red() * (1.0 - a)),
                    int(sc.green() * a + gc.green() * (1.0 - a)),
                    int(sc.blue() * a + gc.blue() * (1.0 - a)),
                ))
            else:
                option.backgroundBrush = QBrush(QColor(42, 109, 181))


class BatchEditTableWidget(QTableWidget):
    """QTableWidget that preserves multi-selection across cell-widget clicks.

    **Problem solved:** In ``ExtendedSelection`` mode, clicking a cell widget
    (e.g. a QComboBox) transfers focus to the widget.  Qt's internal
    ``checkPersistentEditorFocus()`` then calls ``setCurrentIndex(index)``
    which delegates to ``selectionCommand(index, event=None)`` and uses the
    returned ``ClearAndSelect`` flags to destroy the multi-row selection.

    **Fix:** Override ``selectionCommand()`` so that when it is called
    without an event (i.e. from a programmatic ``setCurrentIndex``) and
    there is an active multi-row selection, it returns ``NoUpdate`` instead
    of ``ClearAndSelect``.  This means the current-cell indicator moves to
    the combo's row but the selection stays intact.  All user-initiated
    selection changes (clicks, Ctrl+click, Shift+click) still work
    normally because they always supply a real event.

    Subclasses automatically get:
      - ``batch_selected_keys(key_column=0)`` → ``set[str]`` of item texts
      - ``restore_selection(keys, key_column=0)`` → re-selects by key
      - ``apply_row_color(row, color)`` / ``clear_row_color(row)``
      - Selection-blending delegate (installed automatically)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(_RowTintDelegate(self))

    # ── Row colouring ──────────────────────────────────────────────────────

    def apply_row_color(self, row: int, color: QColor | None):
        """Set *color* as the background for every cell in *row*.

        *color* should already be pre-blended / tinted to the desired
        intensity.  Pass ``None`` to clear the row colour.

        Sets ``BackgroundRole`` on ``QTableWidgetItem`` cells and merges
        ``background-color`` into existing stylesheets on cell widgets.
        """
        if color is not None:
            brush = QBrush(color)
            rgb_str = f"rgb({color.red()}, {color.green()}, {color.blue()})"
        else:
            brush = QBrush()
            rgb_str = None

        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                item.setBackground(brush)
            w = self.cellWidget(row, col)
            if w is not None:
                # Snapshot the widget's original stylesheet on first visit
                base_ss = w.property("_base_ss")
                if base_ss is None:
                    base_ss = w.styleSheet() or ""
                    w.setProperty("_base_ss", base_ss)

                if rgb_str:
                    trimmed = base_ss.rstrip().rstrip("}").rstrip()
                    if trimmed:
                        w.setStyleSheet(
                            f"{trimmed} background-color: {rgb_str}; }}")
                    else:
                        wtype = type(w).__name__
                        w.setStyleSheet(
                            f"{wtype} {{ background-color: {rgb_str}; }}")
                else:
                    w.setStyleSheet(base_ss)

    def clear_row_color(self, row: int):
        """Remove any custom background from *row*."""
        self.apply_row_color(row, None)

    # ── Selection preservation ─────────────────────────────────────────────

    def selectionCommand(self, index, event=None):
        """Prevent ``ClearAndSelect`` when a cell widget receives focus.

        ``checkPersistentEditorFocus()`` calls ``setCurrentIndex(index)``
        which in turn calls ``selectionCommand(index, None)``.  The
        ``None`` event distinguishes this programmatic path from real
        user interactions (mouse/keyboard), which always pass an event.

        The guard is restricted to ``ExtendedSelection`` mode so that
        ``restore_selection()`` (which temporarily switches to
        ``MultiSelection``) can accumulate rows without interference.
        """
        if event is None:
            if self.selectionMode() == QTableWidget.ExtendedSelection:
                if len(self.selectionModel().selectedRows()) > 1:
                    return QItemSelectionModel.NoUpdate
        return super().selectionCommand(index, event)

    # ── Public helpers ────────────────────────────────────────────────────

    def batch_selected_keys(self, key_column: int = 0) -> set[str]:
        """Return key texts for all currently selected rows.

        Parameters
        ----------
        key_column:
            Column whose item text serves as the unique row identifier.
        """
        keys: set[str] = set()
        for idx in self.selectionModel().selectedRows():
            item = self.item(idx.row(), key_column)
            if item:
                keys.add(item.text())
        return keys

    def restore_selection(self, keys: set[str], key_column: int = 0):
        """Re-select rows whose *key_column* text is in *keys*.

        Uses the proven pattern of temporarily switching to
        ``MultiSelection`` mode so that ``selectRow()`` accumulates
        rather than replacing the selection.

        Parameters
        ----------
        keys:
            Set of item texts that identify which rows to select.
        key_column:
            Column whose item text is compared against *keys*.
        """
        if not keys:
            return
        self.clearSelection()
        old_mode = self.selectionMode()
        self.setSelectionMode(QTableWidget.MultiSelection)
        for row in range(self.rowCount()):
            item = self.item(row, key_column)
            if item and item.text() in keys:
                self.selectRow(row)
        self.setSelectionMode(old_mode)


class BatchComboBox(QComboBox):
    """QComboBox that detects Alt+Shift on click for batch-edit mode.

    When the user holds **Alt+Shift** while clicking the combo,
    ``batch_mode`` is set to ``True``.  The connected changed-slot can
    inspect this flag to decide whether to apply the new value to all
    selected rows or just the single row.

    After handling the batch, the slot should reset the flag::

        combo.batch_mode = False
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.batch_mode: bool = False

    def mousePressEvent(self, event):
        mods = QApplication.keyboardModifiers()
        self.batch_mode = bool(
            mods & Qt.AltModifier and mods & Qt.ShiftModifier)
        # Also store as Qt dynamic property so it survives sender()
        # wrapper recreation when slots live on mixin classes.
        self.setProperty("_batch_mode", self.batch_mode)
        super().mousePressEvent(event)


class BatchToolButton(QToolButton):
    """QToolButton that detects Alt+Shift on click for batch-edit mode.

    When the user holds **Alt+Shift** while clicking the button,
    ``batch_mode`` is set to ``True``.  The connected action-slot can
    inspect this flag to decide whether to apply the toggle to all
    selected rows or just the single row.

    After handling the batch, the slot should reset the flag::

        btn.batch_mode = False
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.batch_mode: bool = False

    def mousePressEvent(self, event):
        mods = QApplication.keyboardModifiers()
        self.batch_mode = bool(
            mods & Qt.AltModifier and mods & Qt.ShiftModifier)
        # Also store as Qt dynamic property so it survives sender()
        # wrapper recreation when slots live on mixin classes.
        self.setProperty("_batch_mode", self.batch_mode)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Grid-based color picker
# ---------------------------------------------------------------------------

from PySide6.QtCore import Signal, QPoint
from PySide6.QtWidgets import QGridLayout, QPushButton, QFrame, QScrollArea

import time as _time

from .prefs.param_form import _argb_to_qcolor


def _contrast_text(qc: QColor) -> str:
    """Return '#000000' or '#ffffff' depending on luminance of *qc*."""
    lum = 0.299 * qc.red() + 0.587 * qc.green() + 0.114 * qc.blue()
    return "#000000" if lum > 128 else "#ffffff"


class _ColorCell(QPushButton):
    """One cell in the color grid — shows color name on a colored background."""

    def __init__(self, name: str, argb: str, selected: bool = False,
                 parent=None):
        super().__init__(name, parent)
        qc = _argb_to_qcolor(argb)
        text_col = _contrast_text(qc)
        rgb = f"rgb({qc.red()}, {qc.green()}, {qc.blue()})"
        self.setFixedSize(75, 36)
        self.setCursor(Qt.PointingHandCursor)
        border = "2px solid #ffffff" if selected else "1px solid #222"
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {rgb}; color: {text_col};"
            f"  border: {border}; border-radius: 2px;"
            f"  font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;"
            f"  font-size: 8pt; padding: 1px 2px;"
            f"  text-align: center;"
            f"}}"
            f"QPushButton:hover {{"
            f"  border: 2px solid #ffffff;"
            f"}}"
        )
        self.color_name = name


class ColorGridPopup(QFrame):
    """Popup frame showing colors in a fixed-column grid."""

    colorSelected = Signal(str)
    closed = Signal()

    COLUMNS = 23

    def __init__(self, colors: list[dict[str, str]],
                 selected_name: str = "",
                 selected_argb: str = "",
                 parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setFrameShape(QFrame.Box)
        self.setStyleSheet(
            "ColorGridPopup {"
            "  background-color: #181818;"
            "  border: 2px solid #888;"
            "  border-radius: 3px;"
            "}"
        )

        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(2)

        # Check if selected_name matches any entry
        has_name_match = any(
            e.get("name") == selected_name for e in colors
        ) if selected_name else False

        for i, entry in enumerate(colors):
            name = entry.get("name", "")
            argb = entry.get("argb", "#ff888888")
            row, col = divmod(i, self.COLUMNS)
            # Highlight by name if possible, otherwise by ARGB
            if has_name_match:
                is_selected = bool(name and name == selected_name)
            else:
                is_selected = bool(selected_argb and argb == selected_argb)
            cell = _ColorCell(name, argb, selected=is_selected, parent=self)
            cell.clicked.connect(
                lambda _checked=False, n=name: self._on_pick(n))
            grid.addWidget(cell, row, col)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def _on_pick(self, name: str):
        self.colorSelected.emit(name)
        self.close()


class ColorPickerButton(QPushButton):
    """Button that shows the current color and opens a grid popup on click.

    Drop-in replacement for the QComboBox color pickers in groups tables.
    """

    colorChanged = Signal(str)

    def __init__(self, colors: list[dict[str, str]], parent=None):
        super().__init__(parent)
        self._colors = colors
        self._current = ""
        self._argb_map: dict[str, str] = {
            c["name"]: c.get("argb", "#ff888888") for c in colors
        }
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._show_popup)
        self._last_popup_close = 0.0
        self._update_appearance()

    def currentColor(self) -> str:
        """Return the currently selected color name."""
        return self._current

    def setCurrentColor(self, name: str):
        """Set the current color by name (no signal emitted)."""
        self._current = name
        self._update_appearance()

    def _update_appearance(self):
        """Update button text and background to reflect the current color."""
        argb = self._argb_map.get(self._current)
        if argb:
            qc = _argb_to_qcolor(argb)
            text_col = _contrast_text(qc)
            rgb = f"rgb({qc.red()}, {qc.green()}, {qc.blue()})"
            self.setText(self._current)
            self.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: {rgb}; color: {text_col};"
                f"  border: 1px solid #555; border-radius: 2px;"
                f"  font-size: 8pt; padding: 2px 6px;"
                f"  text-align: left;"
                f"}}"
                f"QPushButton:hover {{ border: 1px solid #aaa; }}"
            )
        else:
            self.setText(self._current or "(no color)")
            self.setStyleSheet(
                "QPushButton { background-color: #3a3a3a; color: #dddddd;"
                " border: 1px solid #555; border-radius: 2px;"
                " font-size: 8pt; padding: 2px 6px; text-align: left; }"
                "QPushButton:hover { border: 1px solid #aaa; }"
            )

    def _show_popup(self):
        # Toggle: suppress reopening if popup just closed (Qt.Popup
        # auto-closes on outside click before our handler runs)
        if _time.monotonic() - self._last_popup_close < 0.3:
            return
        current_argb = self._argb_map.get(self._current, "")
        popup = ColorGridPopup(self._colors, self._current,
                               selected_argb=current_argb, parent=self)
        popup.colorSelected.connect(self._on_selected)
        popup.closed.connect(self._on_popup_closed)
        popup.adjustSize()
        # Center the popup horizontally on the button
        btn_center = self.mapToGlobal(
            QPoint(self.width() // 2, self.height()))
        popup_x = btn_center.x() - popup.sizeHint().width() // 2
        popup_y = btn_center.y()
        # Clamp to screen bounds
        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            popup_w = popup.sizeHint().width()
            popup_h = popup.sizeHint().height()
            popup_x = max(geo.x(), min(popup_x, geo.right() - popup_w))
            popup_y = max(geo.y(), min(popup_y, geo.bottom() - popup_h))
        popup.move(popup_x, popup_y)
        popup.show()

    def _on_popup_closed(self):
        self._last_popup_close = _time.monotonic()

    def _on_selected(self, name: str):
        if name != self._current:
            self._current = name
            self._update_appearance()
            self.colorChanged.emit(name)


class ColorGridPanel(QWidget):
    """Embeddable read-only color grid preview.

    Shows colors in a 23-column matrix. Useful as a palette overview
    in preference pages. Call ``set_colors()`` to refresh.
    Cells stretch horizontally to fill available width.
    """

    colorClicked = Signal(int)

    COLUMNS = 23

    def __init__(self, colors: list[dict[str, str]] | None = None,
                 cell_height: int = 22, parent=None):
        super().__init__(parent)
        self._cell_height = cell_height
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(1)
        if colors:
            self._populate(colors)

    def set_colors(self, colors: list[dict[str, str]]):
        """Refresh the grid with a new color list."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._populate(colors)

    def _populate(self, colors: list[dict[str, str]]):
        from PySide6.QtWidgets import QSizePolicy
        for i, entry in enumerate(colors):
            name = entry.get("name", "")
            argb = entry.get("argb", "#ff888888")
            row, col = divmod(i, self.COLUMNS)
            cell = _ColorCell(name, argb, parent=self)
            cell.setFixedHeight(self._cell_height)
            cell.setMinimumWidth(20)
            cell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cell.setCursor(Qt.PointingHandCursor)
            cell.clicked.connect(
                lambda _checked=False, idx=i: self.colorClicked.emit(idx))
            self._layout.addWidget(cell, row, col)
