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

    def start(self, text: str = "Preparing\u2026"):
        """Reset bar to 0 and show the panel with *text*."""
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
            QTimer.singleShot(self.AUTO_HIDE_MS, self._auto_hide)

    def fail(self, text: str, auto_hide: bool = True):
        """Show failure message and optionally auto-hide."""
        self._label.setText(f"Failed: {text}")
        if auto_hide:
            QTimer.singleShot(self.AUTO_HIDE_MS, self._auto_hide)

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
