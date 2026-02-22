"""Standalone widget classes and helpers used by the main window."""

from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, Signal, QUrl, QMimeData, QPoint
from PySide6.QtGui import QColor, QDrag, QPainter, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QTableWidgetItem,
    QTextBrowser,
    QTreeWidget,
)

from ..theme import COLORS
from ..widgets import BatchEditTableWidget

# ── Constants ────────────────────────────────────────────────────────────────

_TAB_SUMMARY = 0
_TAB_FILE = 1
_TAB_GROUPS = 2
_TAB_SESSION = 3

_PAGE_PROGRESS = 0
_PAGE_TABS = 1

_PHASE_TOPOLOGY = 0
_PHASE_ANALYSIS = 1
_PHASE_SETUP = 2

_SETUP_RIGHT_PLACEHOLDER = 0
_SETUP_RIGHT_TREE = 1

_SEVERITY_SORT = {"PROBLEMS": 0, "Error": 0, "ATTENTION": 1, "OK": 2, "": 3}

_MIME_TRACKS = "application/x-sessionprep-tracks"


# ── Helper functions ─────────────────────────────────────────────────────────

def _make_analysis_cell(html: str, sort_key: int) -> tuple[QLabel, '_SortableItem']:
    """Create a QLabel + hidden sort item for the Analysis column."""
    lbl = QLabel(html)
    lbl.setStyleSheet(
        "QLabel { background: transparent; font-size: 8pt;"
        " font-family: Consolas, monospace; padding: 0 4px; }")
    lbl.setTextFormat(Qt.RichText)
    item = _SortableItem("", sort_key)
    return lbl, item


# ── Widget classes ───────────────────────────────────────────────────────────

class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem with a custom sort key."""

    def __init__(self, text: str, sort_key=None):
        super().__init__(text)
        self._sort_key = sort_key if sort_key is not None else text

    def __lt__(self, other):
        if isinstance(other, _SortableItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)


class _HelpBrowser(QTextBrowser):
    """QTextBrowser that shows detector help tooltips on hover."""

    def __init__(self, help_map: dict[str, str], parent=None):
        super().__init__(parent)
        self._help_map = help_map
        self.setOpenLinks(False)
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        anchor = self.anchorAt(event.pos())
        if anchor.startswith("detector:"):
            det_id = anchor[len("detector:"):]
            html = self._help_map.get(det_id)
            if html:
                from PySide6.QtWidgets import QToolTip
                QToolTip.showText(event.globalPosition().toPoint(), html, self)
            else:
                from PySide6.QtWidgets import QToolTip
                QToolTip.hideText()
        else:
            from PySide6.QtWidgets import QToolTip
            QToolTip.hideText()
        super().mouseMoveEvent(event)


class _DraggableTrackTable(BatchEditTableWidget):
    """BatchEditTableWidget with file-drag support for external applications."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.CopyAction)
        self._source_dir: str | None = None

    def set_source_dir(self, path: str | None):
        self._source_dir = path

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, items):
        if not self._source_dir:
            return super().mimeData(items)
        filenames: set[str] = set()
        for item in items:
            if item.column() == 0 and item.text():
                filenames.add(item.text())
        if not filenames:
            return super().mimeData(items)
        urls = [QUrl.fromLocalFile(os.path.join(self._source_dir, f))
                for f in filenames]
        mime = QMimeData()
        mime.setUrls(urls)
        return mime

    def supportedDragActions(self):
        return Qt.CopyAction


class _SetupDragTable(BatchEditTableWidget):
    """BatchEditTableWidget that produces custom MIME for internal drag."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.CopyAction)

    def mimeTypes(self):
        return [_MIME_TRACKS]

    def mimeData(self, items):
        # Use entry_id from UserRole when available (transfer manifest),
        # fall back to cell text for backward compatibility.
        entry_ids: set[str] = set()
        for item in items:
            if item.column() == 1 and item.text():  # col 1 = File
                eid = item.data(Qt.UserRole)
                entry_ids.add(eid if eid else item.text())
        if not entry_ids:
            return super().mimeData(items)
        mime = QMimeData()
        mime.setData(_MIME_TRACKS, json.dumps(sorted(entry_ids)).encode())
        return mime

    def supportedDragActions(self):
        return Qt.CopyAction

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        mime = self.mimeData(items)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Build a compact, semi-transparent label listing dragged filenames
        filenames = sorted({
            it.text() for it in items if it.column() == 1 and it.text()})
        if not filenames:
            return
        label = "\n".join(filenames[:8])
        if len(filenames) > 8:
            label += f"\n… +{len(filenames) - 8} more"
        fm = self.fontMetrics()
        lines = label.split("\n")
        line_h = fm.height() + 2
        w = max(fm.horizontalAdvance(ln) for ln in lines) + 12
        h = line_h * len(lines) + 6
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setOpacity(0.75)
        painter.fillRect(pix.rect(), QColor(COLORS["accent"]))
        painter.setOpacity(1.0)
        painter.setPen(QColor(COLORS["text"]))
        painter.setFont(self.font())
        y = 3 + fm.ascent()
        for ln in lines:
            painter.drawText(6, y, ln)
            y += line_h
        painter.end()
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(0, 0))
        drag.exec(Qt.CopyAction)


class _FolderDropTree(QTreeWidget):
    """QTreeWidget that accepts track drops onto folder items.

    Supports external drops from the setup table and internal
    drag-and-drop to reorder tracks within / across folders.
    """

    # (filenames, folder_id, insert_index)  -1 = append
    tracks_dropped = Signal(list, str, int)
    tracks_unassigned = Signal(list)  # [filenames]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)

    # -- MIME production (for internal drag of track items) -----------------

    def mimeTypes(self):
        return [_MIME_TRACKS]

    def mimeData(self, items):
        filenames = [
            it.data(0, Qt.UserRole) for it in items
            if it.data(0, Qt.UserRole + 1) == "track"
        ]
        if not filenames:
            return None  # block drag of non-track items (folders)
        mime = QMimeData()
        mime.setData(_MIME_TRACKS, json.dumps(filenames).encode())
        return mime

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction

    # -- Drop handling -----------------------------------------------------

    def _is_valid_mime(self, mimeData) -> bool:
        """Check that the MIME payload is our JSON, not Qt internal data."""
        if not mimeData.hasFormat(_MIME_TRACKS):
            return False
        try:
            bytes(mimeData.data(_MIME_TRACKS)).decode("utf-8")
            return True
        except (UnicodeDecodeError, ValueError):
            return False

    def _resolve_drop(self, pos):
        """Return (folder_id, insert_index) for a drop at *pos*.

        Uses the item geometry to decide above / on / below placement.
        Returns (None, -1) if the drop target is invalid.
        """
        item = self.itemAt(pos)
        if not item:
            return None, -1
        kind = item.data(0, Qt.UserRole + 1)
        if kind == "folder":
            return item.data(0, Qt.UserRole), -1
        if kind == "track":
            parent = item.parent()
            if not parent or parent.data(0, Qt.UserRole + 1) != "folder":
                return None, -1
            folder_id = parent.data(0, Qt.UserRole)
            idx = parent.indexOfChild(item)
            rect = self.visualItemRect(item)
            mid = rect.top() + rect.height() // 2
            if pos.y() > mid:
                idx += 1  # drop below → insert after
            return folder_id, idx
        return None, -1

    def dragEnterEvent(self, event):
        if self._is_valid_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not self._is_valid_mime(event.mimeData()):
            event.ignore()
            return
        folder_id, _ = self._resolve_drop(event.position().toPoint())
        if folder_id is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self._is_valid_mime(event.mimeData()):
            event.ignore()
            return
        pos = event.position().toPoint()
        folder_id, idx = self._resolve_drop(pos)
        if folder_id is None:
            event.ignore()
            return
        data = bytes(event.mimeData().data(_MIME_TRACKS)).decode("utf-8")
        filenames = json.loads(data)
        self.tracks_dropped.emit(filenames, folder_id, idx)
        event.acceptProposedAction()

    # -- Delete to unassign ------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            filenames = []
            for item in self.selectedItems():
                if item.data(0, Qt.UserRole + 1) == "track":
                    filenames.append(item.data(0, Qt.UserRole))
            if filenames:
                self.tracks_unassigned.emit(filenames)
            return
        super().keyPressEvent(event)
