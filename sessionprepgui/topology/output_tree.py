"""Output-tracks tree widget for Phase 1 topology.

Editable QTreeWidget that displays topology output entries with channel
children (and source grandchildren for summed channels).  Accepts drops
from the InputTree and supports internal channel reordering.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..theme import COLORS, FILE_COLOR_OK
from .input_tree import MIME_CHANNEL, COL_NAME, COL_CH, COL_SR, COL_BIT, COL_DUR
from .operations import (
    add_channel,
    append_channels,
    channel_label,
    clear_channel,
    move_channel,
    new_output_file,
    output_names,
    reorder_channel,
    remove_channel,
    remove_output,
    remove_source,
    rename_output,
    sum_channel,
    unique_output_name,
    wire_channel,
    wire_file,
)

if TYPE_CHECKING:
    from sessionpreplib.models import TrackContext
    from sessionpreplib.topology import TopologyMapping

_DIM = QColor(COLORS["dim"])
_EMPTY = "\u2014"  # em-dash for unwired slots
_HIGHLIGHT_BG = QColor(120, 70, 180, 100)  # violet usage highlight
_DROP_TARGET_BG = QColor(120, 80, 180, 100)  # brighter violet for drop target
_TRANSPARENT = QBrush(Qt.NoBrush)

# Internal MIME type for channel reorder within the output tree
MIME_REORDER = "application/x-sessionprep-reorder"


def _format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


class OutputTree(QTreeWidget):
    """Editable tree of topology output entries with drop support."""

    # Emitted after any topology mutation (drop, context-menu action, reorder)
    topology_modified = Signal()

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
        self.itemDoubleClicked.connect(self._on_double_click)
        self.itemChanged.connect(self._on_item_changed)
        self._editing_item: QTreeWidgetItem | None = None
        self._editing_ext: str = ""
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        # Delete key shortcut
        from PySide6.QtGui import QKeySequence, QShortcut
        self._delete_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self)
        self._delete_shortcut.activated.connect(self._on_delete_key)

        self.itemSelectionChanged.connect(self._enforce_single_level)
        self._enforcing = False

        # Stored references — set by mixin before populate()
        self._topo: TopologyMapping | None = None
        self._track_map: dict[str, TrackContext] = {}
        self._drop_target_item: QTreeWidgetItem | None = None
        self._insert_line_y: int | None = None

    # ------------------------------------------------------------------
    # Inline rename (double-click)
    # ------------------------------------------------------------------

    def _on_double_click(self, item, column):
        """Start inline edit on file-level items, stripping the extension."""
        data = item.data(COL_NAME, Qt.UserRole)
        if not data or data[0] != "file" or column != COL_NAME:
            return
        filename = data[1]
        stem, ext = os.path.splitext(filename)
        self._editing_item = item
        self._editing_ext = ext
        self.blockSignals(True)
        item.setText(COL_NAME, stem)
        self.blockSignals(False)
        self.editItem(item, COL_NAME)

    def _on_item_changed(self, item, column):
        """Commit the inline rename when editing finishes."""
        if item is not self._editing_item or column != COL_NAME:
            return
        new_stem = item.text(COL_NAME).strip()
        ext = self._editing_ext
        self._editing_item = None
        self._editing_ext = ""

        old_data = item.data(COL_NAME, Qt.UserRole)
        if not old_data or old_data[0] != "file":
            return
        old_filename = old_data[1]
        old_stem = os.path.splitext(old_filename)[0]

        if not new_stem or new_stem == old_stem:
            # Cancelled or unchanged — restore original text
            self.blockSignals(True)
            item.setText(COL_NAME, old_filename)
            self.blockSignals(False)
            return

        new_filename = new_stem + ext
        if not rename_output(self._topo, old_filename, new_filename):
            QMessageBox.warning(
                self, "Rename Output",
                f"An output named '{new_filename}' already exists.")
            self.blockSignals(True)
            item.setText(COL_NAME, old_filename)
            self.blockSignals(False)
            return

        # Update item data so _save_state captures the new key
        self.blockSignals(True)
        item.setText(COL_NAME, new_filename)
        item.setData(COL_NAME, Qt.UserRole, ("file", new_filename))
        self.blockSignals(False)
        self.topology_modified.emit()

    # ------------------------------------------------------------------
    # Delete key
    # ------------------------------------------------------------------

    def _on_delete_key(self):
        """Remove selected channel(s), source(s), or file(s) when Delete is pressed."""
        if not self._topo:
            return
        items = self.selectedItems()
        if not items:
            return

        # Collect removals by type, keyed by data tuples
        file_removals: list[str] = []
        # {output_filename: [ch_index, ...]} — will sort descending later
        channel_removals: dict[str, list[int]] = {}
        source_removals: list[tuple[str, int, str, int]] = []

        for item in items:
            data = item.data(COL_NAME, Qt.UserRole)
            if not data:
                continue
            if data[0] == "file":
                file_removals.append(data[1])
            elif data[0] == "channel":
                _, output_filename, target_ch = data
                channel_removals.setdefault(output_filename, []).append(
                    target_ch)
            elif data[0] == "source":
                _, ofn, tch, ifn, sch = data
                source_removals.append((ofn, tch, ifn, sch))

        if not file_removals and not channel_removals and not source_removals:
            return

        # 1) Remove sources first (doesn't affect channel numbering)
        for ofn, tch, ifn, sch in source_removals:
            remove_source(self._topo, ofn, tch, ifn, sch)

        # 2) Remove channels in descending order per file so indices stay valid
        for ofn, chs in channel_removals.items():
            if ofn in file_removals:
                continue  # whole file will be removed anyway
            for ch in sorted(set(chs), reverse=True):
                remove_channel(self._topo, ofn, ch)

        # 3) Remove whole files
        for ofn in file_removals:
            remove_output(self._topo, ofn)

        self.topology_modified.emit()

    # ------------------------------------------------------------------
    # Populate
    # ------------------------------------------------------------------

    def populate(
        self,
        topology: TopologyMapping | None,
        track_map: dict[str, TrackContext],
    ) -> None:
        """Rebuild the tree from *topology*."""
        state = self._save_state()
        self._topo = topology
        self._track_map = track_map
        self.clear()

        if not topology:
            return

        for entry in topology.entries:
            file_item = self._build_file_item(entry)
            self.addTopLevelItem(file_item)

        self.expandAll()
        self._restore_state(state)

    def _build_file_item(self, entry) -> QTreeWidgetItem:
        """Build a top-level file item with channel children."""
        file_item = QTreeWidgetItem()
        file_item.setFlags(
            (file_item.flags() | Qt.ItemIsDropEnabled | Qt.ItemIsEditable)
            & ~Qt.ItemIsDragEnabled)
        file_item.setText(COL_NAME, entry.output_filename)
        file_item.setForeground(COL_NAME, FILE_COLOR_OK)
        file_item.setText(COL_CH, str(entry.output_channels))
        file_item.setForeground(COL_CH, _DIM)

        # Derive metadata from first source track
        meta_track = self._resolve_metadata(entry)
        if meta_track:
            file_item.setText(COL_SR, str(meta_track.samplerate))
            file_item.setForeground(COL_SR, _DIM)
            file_item.setText(COL_BIT, meta_track.bitdepth or "")
            file_item.setForeground(COL_BIT, _DIM)
            file_item.setText(COL_DUR, _format_duration(meta_track.duration_sec))
            file_item.setForeground(COL_DUR, _DIM)

        file_item.setData(
            COL_NAME, Qt.UserRole, ("file", entry.output_filename))

        # Build per-channel children
        for ch in range(entry.output_channels):
            ch_item = self._build_channel_item(entry, ch)
            file_item.addChild(ch_item)

        return file_item

    def _build_channel_item(self, entry, target_ch: int) -> QTreeWidgetItem:
        """Build a channel child, possibly with source grandchildren."""
        label = channel_label(target_ch, entry.output_channels)

        # Collect all routes targeting this channel
        sources = []
        for src in entry.sources:
            for route in src.routes:
                if route.target_channel == target_ch:
                    sources.append((src.input_filename, route.source_channel))

        ch_item = QTreeWidgetItem()
        ch_item.setFlags(ch_item.flags() | Qt.ItemIsDragEnabled)
        ch_item.setData(
            COL_NAME, Qt.UserRole,
            ("channel", entry.output_filename, target_ch))

        if len(sources) == 0:
            # Empty / silent channel
            ch_item.setText(COL_NAME, f"{target_ch} ({label}): {_EMPTY}")
            ch_item.setForeground(COL_NAME, _DIM)
        elif len(sources) == 1:
            # Single source — show inline
            inp_fn, src_ch = sources[0]
            ch_item.setText(
                COL_NAME,
                f"{target_ch} ({label}): {inp_fn} [ch{src_ch}]")
            ch_item.setForeground(COL_NAME, QColor(COLORS["clean"]))
        else:
            # Multiple sources (summed) — channel node with children
            ch_item.setText(COL_NAME, f"{target_ch} ({label}):")
            ch_item.setForeground(COL_NAME, QColor(COLORS["information"]))
            for inp_fn, src_ch in sources:
                src_item = QTreeWidgetItem()
                src_item.setText(COL_NAME, f"{inp_fn} [ch{src_ch}]")
                src_item.setForeground(COL_NAME, QColor(COLORS["clean"]))
                src_item.setData(
                    COL_NAME, Qt.UserRole,
                    ("source", entry.output_filename, target_ch,
                     inp_fn, src_ch))
                ch_item.addChild(src_item)

        return ch_item

    def _resolve_metadata(self, entry) -> TrackContext | None:
        """Get metadata track from the first source of an entry."""
        if entry.sources:
            return self._track_map.get(entry.sources[0].input_filename)
        return None

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
            self._select_by_keys(key_set)
            self.blockSignals(False)

        self.verticalScrollBar().setValue(state.get("scroll", 0))

    def _select_by_keys(self, key_set: set[tuple]) -> None:
        """Select items whose UserRole data is in *key_set*."""
        for i in range(self.topLevelItemCount()):
            fi = self.topLevelItem(i)
            data = fi.data(COL_NAME, Qt.UserRole)
            if data and data in key_set:
                fi.setSelected(True)
            for j in range(fi.childCount()):
                ch = fi.child(j)
                cd = ch.data(COL_NAME, Qt.UserRole)
                if cd and cd in key_set:
                    ch.setSelected(True)
                for k in range(ch.childCount()):
                    src = ch.child(k)
                    sd = src.data(COL_NAME, Qt.UserRole)
                    if sd and sd in key_set:
                        src.setSelected(True)

    # ------------------------------------------------------------------
    # Usage highlighting
    # ------------------------------------------------------------------

    def highlight_usages(
        self,
        input_filename: str | None,
        source_channel: int | None = None,
    ) -> None:
        """Set a background tint on output items that reference the given input.

        If *source_channel* is ``None``, all channels of *input_filename* match.
        Call :meth:`clear_highlights` to remove.
        """
        self.clear_highlights()
        if input_filename is None:
            return
        hl = QBrush(_HIGHLIGHT_BG)
        for i in range(self.topLevelItemCount()):
            fi = self.topLevelItem(i)
            for j in range(fi.childCount()):
                ch_item = fi.child(j)
                if self._item_references(ch_item, input_filename,
                                         source_channel):
                    self._set_row_bg(ch_item, hl)
                # Also check grandchildren (source items in summed channels)
                for k in range(ch_item.childCount()):
                    src_item = ch_item.child(k)
                    if self._item_references(src_item, input_filename,
                                             source_channel):
                        self._set_row_bg(src_item, hl)

    def clear_highlights(self) -> None:
        """Remove all usage-highlight backgrounds."""
        for i in range(self.topLevelItemCount()):
            fi = self.topLevelItem(i)
            self._set_row_bg(fi, _TRANSPARENT)
            for j in range(fi.childCount()):
                ch = fi.child(j)
                self._set_row_bg(ch, _TRANSPARENT)
                for k in range(ch.childCount()):
                    self._set_row_bg(ch.child(k), _TRANSPARENT)

    def _set_row_bg(self, item: QTreeWidgetItem, brush) -> None:
        """Apply *brush* to every column of *item*."""
        for col in range(self.columnCount()):
            item.setBackground(col, brush)

    @staticmethod
    def _item_references(
        item: QTreeWidgetItem,
        input_filename: str,
        source_channel: int | None,
    ) -> bool:
        """Return True if *item*'s UserRole data references the given input."""
        data = item.data(COL_NAME, Qt.UserRole)
        if not data:
            return False
        # Channel item: ("channel", output_fn, target_ch)
        #   — we need to check the topology, but the display text encodes the
        #     source info.  Instead, look at the text for a quick match.
        # Source item: ("source", output_fn, target_ch, inp_fn, src_ch)
        if data[0] == "source":
            _, _ofn, _tch, ifn, sch = data
            if ifn != input_filename:
                return False
            return source_channel is None or sch == source_channel
        if data[0] == "channel":
            # Single-source channels encode source in display text
            text = item.text(COL_NAME)
            if input_filename not in text:
                return False
            if source_channel is not None:
                return f"[ch{source_channel}]" in text
            return True
        return False

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
    # Drag support (internal reorder)
    # ------------------------------------------------------------------

    def mimeTypes(self):
        return [MIME_REORDER, MIME_CHANNEL]

    def mimeData(self, items):
        """Encode dragged channel items for internal reorder."""
        payload = []
        for item in items:
            data = item.data(COL_NAME, Qt.UserRole)
            if data and data[0] == "channel":
                payload.append({
                    "output_filename": data[1],
                    "channel": data[2],
                })
        if not payload:
            return None
        mime = QMimeData()
        mime.setData(MIME_REORDER,
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
        rect = self.visualItemRect(items[0])
        pixmap = QPixmap(self.viewport().size())
        pixmap.fill(Qt.transparent)
        self.viewport().render(pixmap, rect.topLeft(), rect)
        pixmap = pixmap.copy(0, 0, rect.width(), rect.height())
        faded = QPixmap(pixmap.size())
        faded.fill(Qt.transparent)
        painter = QPainter(faded)
        painter.setOpacity(0.5)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        drag.setPixmap(faded)
        drag.setHotSpot(QPoint(rect.width() // 2, rect.height() // 2))
        drag.exec(Qt.MoveAction)

    def supportedDropActions(self):
        return Qt.MoveAction | Qt.CopyAction

    # ------------------------------------------------------------------
    # Drop-target highlight + insert line
    # ------------------------------------------------------------------

    def _set_drop_target(self, item: QTreeWidgetItem) -> None:
        """Highlight *item* as the current drop target."""
        if item is self._drop_target_item:
            return
        self._clear_drop_target()
        self._drop_target_item = item
        hl = QBrush(_DROP_TARGET_BG)
        self._set_row_bg(item, hl)

    def _clear_drop_target(self) -> None:
        """Remove drop-target highlight from the previous item."""
        if self._drop_target_item is not None:
            self._set_row_bg(self._drop_target_item, _TRANSPARENT)
            self._drop_target_item = None
        if self._insert_line_y is not None:
            self._insert_line_y = None
            self.viewport().update()

    def _update_insert_line(self, item, pos_y: int) -> None:
        """Compute the y-coordinate for the insert-position indicator."""
        if item is None:
            if self._insert_line_y is not None:
                self._insert_line_y = None
                self.viewport().update()
            return
        rect = self.visualItemRect(item)
        mid = rect.top() + rect.height() // 2
        # Above or below the midpoint of the hovered item
        if pos_y < mid:
            y = rect.top()
        else:
            y = rect.bottom()
        if y != self._insert_line_y:
            self._insert_line_y = y
            self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._insert_line_y is not None:
            painter = QPainter(self.viewport())
            pen = QPen(QColor(255, 255, 255, 200), 2)
            painter.setPen(pen)
            w = self.viewport().width()
            painter.drawLine(0, self._insert_line_y, w, self._insert_line_y)
            painter.end()

    # ------------------------------------------------------------------
    # Drop handling
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if (event.mimeData().hasFormat(MIME_REORDER)
                or event.mimeData().hasFormat(MIME_CHANNEL)):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat(MIME_REORDER) or mime.hasFormat(MIME_CHANNEL):
            item = self.itemAt(event.position().toPoint())
            if item:
                data = item.data(COL_NAME, Qt.UserRole)
                if data and data[0] in ("file", "channel"):
                    # For reorder drags, only allow channel-level targets
                    if mime.hasFormat(MIME_REORDER):
                        if data[0] != "channel":
                            self._clear_drop_target()
                            event.ignore()
                            return
                        self._clear_drop_target()
                        self._update_insert_line(
                            item, int(event.position().y()))
                    else:
                        self._set_drop_target(item)
                        if self._insert_line_y is not None:
                            self._insert_line_y = None
                            self.viewport().update()
                    event.acceptProposedAction()
                    return
            self._clear_drop_target()
            event.ignore()
        else:
            self._clear_drop_target()
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._clear_drop_target()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._clear_drop_target()
        mime = event.mimeData()

        # Internal reorder
        if mime.hasFormat(MIME_REORDER):
            self._handle_reorder_drop(event)
            return

        # External drop from input tree
        if mime.hasFormat(MIME_CHANNEL):
            self._handle_external_drop(event)
            return

        super().dropEvent(event)

    def _resolve_drop_target(self, event):
        """Return (to_filename, to_channel_index) from drop position.

        Uses cursor y vs item midpoint to decide above/below insertion.
        Returns ``None`` on invalid targets.
        """
        target_item = self.itemAt(event.position().toPoint())
        if not target_item:
            return None
        target_data = target_item.data(COL_NAME, Qt.UserRole)
        if not target_data:
            return None

        if target_data[0] == "channel":
            to_fn = target_data[1]
            to_ch = target_data[2]
            # Above/below midpoint decides insert index
            rect = self.visualItemRect(target_item)
            mid = rect.top() + rect.height() // 2
            if int(event.position().y()) > mid:
                to_ch += 1
            return to_fn, to_ch
        elif target_data[0] == "file":
            to_fn = target_data[1]
            entry = next((e for e in self._topo.entries
                          if e.output_filename == to_fn), None)
            if entry is None:
                return None
            # Dropped on file header → append at end
            return to_fn, entry.output_channels
        return None

    def _handle_reorder_drop(self, event):
        """Handle internal channel reorder / cross-file move drag."""
        if not self._topo:
            event.ignore()
            return

        raw = bytes(event.mimeData().data(MIME_REORDER)).decode("utf-8")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            event.ignore()
            return
        if not payload or len(payload) != 1:
            event.ignore()
            return

        from_fn = payload[0]["output_filename"]
        from_ch = payload[0]["channel"]

        target = self._resolve_drop_target(event)
        if target is None:
            event.ignore()
            return
        to_fn, to_ch = target

        if from_fn == to_fn:
            # Same file — simple reorder
            if from_ch == to_ch or from_ch + 1 == to_ch:
                event.ignore()
                return
            # Adjust for the "insert before" semantic: if inserting after
            # the dragged item's original position, subtract 1
            final_ch = to_ch if to_ch < from_ch else to_ch - 1
            event.acceptProposedAction()
            reorder_channel(self._topo, from_fn, from_ch, final_ch)
        else:
            # Cross-file move
            event.acceptProposedAction()
            move_channel(self._topo, from_fn, from_ch, to_fn, to_ch)

        self.topology_modified.emit()

    def _handle_external_drop(self, event):
        """Handle drop from the input tree."""
        if not self._topo:
            event.ignore()
            return

        raw = bytes(event.mimeData().data(MIME_CHANNEL)).decode("utf-8")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            event.ignore()
            return

        if not payload:
            event.ignore()
            return

        target_item = self.itemAt(event.position().toPoint())
        if not target_item:
            event.ignore()
            return

        target_data = target_item.data(COL_NAME, Qt.UserRole)
        if not target_data:
            event.ignore()
            return

        event.acceptProposedAction()

        if target_data[0] == "file":
            self._drop_on_file(target_data, payload)
        elif target_data[0] == "channel":
            self._drop_on_channel(target_data, payload)

    def _drop_on_file(self, target_data, payload: list[dict]):
        """Handle drop of channels onto a file node → append as new channels."""
        _, output_filename = target_data

        if not payload:
            return

        channels = [
            (item["input_filename"], item["source_channel"])
            for item in payload
        ]
        append_channels(self._topo, output_filename, channels)
        self.topology_modified.emit()

    def _drop_on_channel(self, target_data, payload: list[dict]):
        """Handle drop of channel(s) onto a channel node."""
        _, output_filename, target_ch = target_data

        if len(payload) != 1:
            # Multi-channel drop onto single channel slot — only use first
            pass

        item = payload[0]
        input_filename = item["input_filename"]
        source_ch = item["source_channel"]

        # Check if the channel is already occupied
        entry = None
        for e in self._topo.entries:
            if e.output_filename == output_filename:
                entry = e
                break
        if entry is None:
            return

        # Count existing sources for this target channel
        existing_sources = []
        for src in entry.sources:
            for route in src.routes:
                if route.target_channel == target_ch:
                    existing_sources.append(
                        (src.input_filename, route.source_channel))

        if not existing_sources:
            # Empty slot — just wire
            wire_channel(self._topo, output_filename, target_ch,
                         input_filename, source_ch)
        else:
            # Occupied — ask Replace or Sum
            dlg = QMessageBox(self)
            dlg.setWindowTitle("Channel Occupied")
            dlg.setText(
                f"Channel {target_ch} already has source(s).\n\n"
                "Replace existing or sum together?")
            btn_replace = dlg.addButton("Replace", QMessageBox.AcceptRole)
            btn_sum = dlg.addButton("Sum", QMessageBox.ActionRole)
            dlg.addButton("Cancel", QMessageBox.RejectRole)
            dlg.exec()
            clicked = dlg.clickedButton()
            if clicked == btn_replace:
                wire_channel(self._topo, output_filename, target_ch,
                             input_filename, source_ch)
            elif clicked == btn_sum:
                sum_channel(self._topo, output_filename, target_ch,
                            input_filename, source_ch)
            else:
                return

        self.topology_modified.emit()

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)

        if item is None:
            # Background — New Output File
            self._menu_background(pos)
            return

        data = item.data(COL_NAME, Qt.UserRole)
        if not data:
            return

        if data[0] == "file":
            self._menu_file(pos, data)
        elif data[0] == "channel":
            self._menu_channel(pos, data)
        elif data[0] == "source":
            self._menu_source(pos, data)

    def _menu_background(self, pos):
        """Context menu on empty area."""
        if not self._topo:
            return
        menu = QMenu(self)
        act = menu.addAction("New Output File\u2026")
        act.triggered.connect(self._action_new_output_file)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _menu_file(self, pos, data):
        """Context menu on a file-level item."""
        if not self._topo:
            return
        _, output_filename = data
        menu = QMenu(self)

        act_add = menu.addAction("Add Channel")
        act_add.triggered.connect(
            lambda checked, fn=output_filename: self._action_add_channel(fn))

        menu.addSeparator()
        act_remove = menu.addAction("Remove Output")
        act_remove.triggered.connect(
            lambda checked, fn=output_filename: self._action_remove_output(fn))

        menu.exec(self.viewport().mapToGlobal(pos))

    def _menu_channel(self, pos, data):
        """Context menu on a channel-level item."""
        if not self._topo:
            return
        _, output_filename, target_ch = data
        menu = QMenu(self)

        act_clear = menu.addAction("Clear Source")
        act_clear.triggered.connect(
            lambda checked, fn=output_filename, ch=target_ch:
                self._action_clear_channel(fn, ch))

        menu.addSeparator()
        act_remove = menu.addAction("Remove Channel")
        act_remove.triggered.connect(
            lambda checked, fn=output_filename, ch=target_ch:
                self._action_remove_channel(fn, ch))

        menu.exec(self.viewport().mapToGlobal(pos))

    def _menu_source(self, pos, data):
        """Context menu on a source-level item (grandchild under summed channel)."""
        if not self._topo:
            return
        _, output_filename, target_ch, input_filename, source_ch = data
        menu = QMenu(self)

        act = menu.addAction("Remove Source")
        act.triggered.connect(
            lambda checked, ofn=output_filename, tch=target_ch,
                   ifn=input_filename, sch=source_ch:
                self._action_remove_source(ofn, tch, ifn, sch))

        menu.exec(self.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Context-menu actions
    # ------------------------------------------------------------------

    def _action_new_output_file(self):
        if not self._topo:
            return
        name, ok = QInputDialog.getText(
            self, "New Output File", "Filename (with extension):")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in output_names(self._topo):
            QMessageBox.warning(
                self, "New Output File",
                f"An output named '{name}' already exists.")
            return
        channels, ok2 = QInputDialog.getInt(
            self, "New Output File", "Number of channels:", 1, 1, 64)
        if not ok2:
            return
        new_output_file(self._topo, name, channels)
        self.topology_modified.emit()

    def _action_add_channel(self, output_filename: str):
        if not self._topo:
            return
        count = self._ask_channel_count()
        if count < 1:
            return
        for _ in range(count):
            add_channel(self._topo, output_filename)
        self.topology_modified.emit()

    def _ask_channel_count(self) -> int:
        """Show a small dialog with a spinner for channel count. Enter confirms."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Channels")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Number of channels to add:"))
        spin = QSpinBox()
        spin.setRange(1, 64)
        spin.setValue(1)
        spin.selectAll()
        layout.addWidget(spin)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        spin.setFocus()
        if dlg.exec() == QDialog.Accepted:
            return spin.value()
        return 0

    def _action_remove_output(self, output_filename: str):
        if not self._topo:
            return
        remove_output(self._topo, output_filename)
        self.topology_modified.emit()

    def _action_clear_channel(self, output_filename: str, target_ch: int):
        if not self._topo:
            return
        clear_channel(self._topo, output_filename, target_ch)
        self.topology_modified.emit()

    def _action_remove_channel(self, output_filename: str, target_ch: int):
        if not self._topo:
            return
        remove_channel(self._topo, output_filename, target_ch)
        self.topology_modified.emit()

    def _action_remove_source(
        self,
        output_filename: str,
        target_ch: int,
        input_filename: str,
        source_ch: int,
    ):
        if not self._topo:
            return
        remove_source(
            self._topo, output_filename, target_ch,
            input_filename, source_ch)
        self.topology_modified.emit()
