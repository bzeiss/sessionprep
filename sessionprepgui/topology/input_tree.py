"""Input-tracks tree widget for Phase 1 topology.

Read-only QTreeWidget that displays source audio files with expandable
channel children.  Supports drag of individual channels or whole files.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from ..theme import COLORS, FILE_COLOR_OK
from .operations import channel_label, used_channels

if TYPE_CHECKING:
    from sessionpreplib.models import TrackContext
    from sessionpreplib.topology import TopologyMapping

# MIME type used for cross-tree drag-and-drop
MIME_CHANNEL = "application/x-sessionprep-channel"

# Column indices
COL_NAME = 0
COL_CH = 1
COL_SR = 2
COL_BIT = 3
COL_DUR = 4

_DIM = QColor(COLORS["dim"])


def _format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


class InputTree(QTreeWidget):
    """Read-only tree of source tracks with draggable channel children."""

    # Emitted on right-click: (filename, selected_filenames, global_pos)
    context_menu_requested = Signal(str, list, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setColumnCount(5)
        self.setHeaderLabels(["File", "Ch", "SR", "Bit", "Duration"])
        self.header().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        h = self.header()
        h.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for col in (COL_CH, COL_SR, COL_BIT, COL_DUR):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self.itemSelectionChanged.connect(self._enforce_single_level)
        self._enforcing = False

    # ------------------------------------------------------------------
    # Single-level selection
    # ------------------------------------------------------------------

    def _enforce_single_level(self):
        """Deselect items at a different tree depth than the clicked item."""
        if self._enforcing:
            return
        items = self.selectedItems()
        if len(items) <= 1:
            return
        # Use the current (clicked) item's depth as the anchor level
        anchor = self.currentItem()
        if anchor is None:
            return
        anchor_depth = self._item_depth(anchor)
        wrong = [it for it in items if self._item_depth(it) != anchor_depth]
        if not wrong:
            return
        self._enforcing = True
        self.blockSignals(True)
        for it in wrong:
            it.setSelected(False)
        self.blockSignals(False)
        self._enforcing = False

    @staticmethod
    def _item_depth(item) -> int:
        depth = 0
        p = item.parent()
        while p:
            depth += 1
            p = p.parent()
        return depth

    # ------------------------------------------------------------------
    # Populate
    # ------------------------------------------------------------------

    def populate(
        self,
        source_tracks: list[TrackContext],
        topology: TopologyMapping | None = None,
    ) -> None:
        """Rebuild the tree from *source_tracks*."""
        state = self._save_state()
        self.clear()
        ok_tracks = [t for t in source_tracks if t.status == "OK"]
        used = used_channels(topology) if topology else set()

        for track in ok_tracks:
            # Check if ALL channels of this file are used
            all_used = all(
                (track.filename, ch) in used
                for ch in range(track.channels)
            )
            any_used = any(
                (track.filename, ch) in used
                for ch in range(track.channels)
            )

            file_color = _DIM if all_used else FILE_COLOR_OK

            file_item = QTreeWidgetItem()
            file_item.setText(COL_NAME, track.filename)
            file_item.setForeground(COL_NAME, file_color)
            file_item.setText(COL_CH, str(track.channels))
            file_item.setForeground(COL_CH, _DIM)
            file_item.setText(COL_SR, str(track.samplerate))
            file_item.setForeground(COL_SR, _DIM)
            file_item.setText(COL_BIT, track.bitdepth or "")
            file_item.setForeground(COL_BIT, _DIM)
            file_item.setText(COL_DUR, _format_duration(track.duration_sec))
            file_item.setForeground(COL_DUR, _DIM)
            file_item.setData(
                COL_NAME, Qt.UserRole, ("file", track.filename))
            file_item.setFlags(
                file_item.flags() | Qt.ItemIsDragEnabled)

            # Channel children
            for ch in range(track.channels):
                label = channel_label(ch, track.channels)
                ch_item = QTreeWidgetItem()
                ch_text = f"{ch} ({label}): {track.filename}"
                ch_item.setText(COL_NAME, ch_text)
                ch_used = (track.filename, ch) in used
                ch_item.setForeground(
                    COL_NAME, _DIM if ch_used else FILE_COLOR_OK)
                ch_item.setData(
                    COL_NAME, Qt.UserRole,
                    ("channel", track.filename, ch))
                ch_item.setFlags(
                    ch_item.flags() | Qt.ItemIsDragEnabled)
                file_item.addChild(ch_item)

            self.addTopLevelItem(file_item)

        # Expand all by default so channels are visible
        self.expandAll()
        self._restore_state(state)

    # ------------------------------------------------------------------
    # State save / restore
    # ------------------------------------------------------------------

    def _save_state(self) -> dict:
        """Capture scroll, selection, and expanded state before rebuild."""
        scroll_val = self.verticalScrollBar().value()
        selected_keys: list[tuple] = []
        for item in self.selectedItems():
            data = item.data(COL_NAME, Qt.UserRole)
            if data:
                selected_keys.append(data)
        collapsed: set[str] = set()
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if not item.isExpanded():
                data = item.data(COL_NAME, Qt.UserRole)
                if data and data[0] == "file":
                    collapsed.add(data[1])
        return {"scroll": scroll_val, "selected": selected_keys,
                "collapsed": collapsed}

    def _restore_state(self, state: dict) -> None:
        """Reapply scroll, selection, and expanded state after rebuild."""
        collapsed = state.get("collapsed", set())
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            data = item.data(COL_NAME, Qt.UserRole)
            if data and data[0] == "file" and data[1] in collapsed:
                item.setExpanded(False)

        selected_keys = state.get("selected", [])
        if selected_keys:
            key_set = set(selected_keys)
            self.blockSignals(True)
            for i in range(self.topLevelItemCount()):
                item = self.topLevelItem(i)
                data = item.data(COL_NAME, Qt.UserRole)
                if data and data in key_set:
                    item.setSelected(True)
                for j in range(item.childCount()):
                    child = item.child(j)
                    cd = child.data(COL_NAME, Qt.UserRole)
                    if cd and cd in key_set:
                        child.setSelected(True)
            self.blockSignals(False)

        self.verticalScrollBar().setValue(state.get("scroll", 0))

    # ------------------------------------------------------------------
    # Drag support
    # ------------------------------------------------------------------

    def mimeTypes(self):
        return [MIME_CHANNEL]

    def mimeData(self, items):
        """Encode dragged items as JSON list of channel descriptors."""
        payload = []
        for item in items:
            data = item.data(COL_NAME, Qt.UserRole)
            if not data:
                continue
            if data[0] == "channel":
                _, filename, ch = data
                payload.append({
                    "input_filename": filename,
                    "source_channel": ch,
                    "drag_type": "channel",
                })
            elif data[0] == "file":
                _, filename = data
                # Encode all channels of this file
                for i in range(item.childCount()):
                    child = item.child(i)
                    cd = child.data(COL_NAME, Qt.UserRole)
                    if cd and cd[0] == "channel":
                        payload.append({
                            "input_filename": cd[1],
                            "source_channel": cd[2],
                            "drag_type": "file",
                        })
        if not payload:
            return None

        mime = QMimeData()
        mime.setData(MIME_CHANNEL,
                     QByteArray(json.dumps(payload).encode("utf-8")))
        return mime

    def startDrag(self, supportedActions):
        """Start drag with a semi-transparent pixmap."""
        items = self.selectedItems()
        if not items:
            return
        mime = self.mimeData(items)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Render the first item's row as a pixmap at 50% opacity
        rect = self.visualItemRect(items[0])
        pixmap = QPixmap(self.viewport().size())
        pixmap.fill(Qt.transparent)
        self.viewport().render(pixmap, rect.topLeft(), rect)
        pixmap = pixmap.copy(0, 0, rect.width(), rect.height())
        # Apply 50% transparency
        faded = QPixmap(pixmap.size())
        faded.fill(Qt.transparent)
        from PySide6.QtGui import QPainter
        painter = QPainter(faded)
        painter.setOpacity(0.5)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        drag.setPixmap(faded)
        drag.setHotSpot(QPoint(rect.width() // 2, rect.height() // 2))
        drag.exec(Qt.CopyAction)

    def supportedDragActions(self):
        return Qt.CopyAction

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        data = item.data(COL_NAME, Qt.UserRole)
        if not data or data[0] != "file":
            return
        filename = data[1]
        # Collect all selected filenames (file-level only)
        selected = []
        for sel_item in self.selectedItems():
            sd = sel_item.data(COL_NAME, Qt.UserRole)
            if sd and sd[0] == "file":
                selected.append(sd[1])
        global_pos = self.viewport().mapToGlobal(pos)
        self.context_menu_requested.emit(filename, selected, global_pos)
