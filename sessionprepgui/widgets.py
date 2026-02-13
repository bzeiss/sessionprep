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

from PySide6.QtCore import Qt, QItemSelectionModel
from PySide6.QtWidgets import QApplication, QComboBox, QTableWidget


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
    """

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
        super().mousePressEvent(event)
