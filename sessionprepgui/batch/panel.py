from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from PySide6.QtCore import Qt, Signal, Slot, QPoint
from PySide6.QtGui import QPainter, QPen, QColor, QDropEvent, QDrag, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from ..theme import COLORS


@dataclass
class BatchItem:
    id: str
    project_name: str
    daw_processor_id: str
    daw_processor_name: str
    output_path: str
    session_state: dict[str, Any]
    status: str = "Pending"
    result_text: str = ""


class _BatchTable(QTableWidget):
    """Table widget with internal drag-and-drop row reordering."""

    reordered = Signal(int, int)  # source_row, target_row

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.SingleSelection)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(False)
        
        self._insert_line_y: int | None = None

    def startDrag(self, supportedActions):
        selected = self.selectedItems()
        if not selected:
            return
            
        row = selected[0].row()
        
        # Calculate the bounding rect of the entire row
        rect = self.visualRect(self.model().index(row, 0))
        for col in range(1, self.columnCount()):
            rect = rect.united(self.visualRect(self.model().index(row, col)))
            
        # Render the row into a pixmap
        pixmap = QPixmap(rect.size())
        pixmap.fill(Qt.transparent)
        self.viewport().render(pixmap, QPoint(0, 0), rect)
        
        # Create a new pixmap with 50% opacity
        transparent_pixmap = QPixmap(pixmap.size())
        transparent_pixmap.fill(Qt.transparent)
        
        painter = QPainter(transparent_pixmap)
        painter.setOpacity(0.5)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        
        # Start the drag operation
        drag = QDrag(self)
        mime = self.model().mimeData(self.selectedIndexes())
        drag.setMimeData(mime)
        drag.setPixmap(transparent_pixmap)
        
        # Get mouse position relative to the row's top-left so the drag image aligns correctly
        mouse_pos = self.viewport().mapFromGlobal(self.cursor().pos())
        hotspot = mouse_pos - rect.topLeft()
        drag.setHotSpot(hotspot)
        
        drag.exec_(supportedActions)

    def dragMoveEvent(self, event):
        if event.source() != self:
            event.ignore()
            return
            
        event.setDropAction(Qt.MoveAction)
        event.accept()
        
        pos = event.position().toPoint()
        row = self.rowAt(pos.y())
        
        if row == -1:
            # Hovering below the last row
            last_row = self.rowCount() - 1
            if last_row >= 0:
                rect = self.visualRect(self.model().index(last_row, 0))
                self._insert_line_y = rect.bottom()
            else:
                self._insert_line_y = None
        else:
            rect = self.visualRect(self.model().index(row, 0))
            mid = rect.top() + rect.height() // 2
            if pos.y() < mid:
                self._insert_line_y = rect.top()
            else:
                self._insert_line_y = rect.bottom()
                
        self.viewport().update()
        
    def dragEnterEvent(self, event):
        if event.source() == self:
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._insert_line_y = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self._insert_line_y = None
        self.viewport().update()
        
        if event.source() != self:
            event.ignore()
            return

        selected = self.selectedItems()
        if not selected:
            event.ignore()
            return
            
        source_row = selected[0].row()
        pos = event.position().toPoint()
        target_row = self.rowAt(pos.y())
        
        if target_row == -1:
            target_row = self.rowCount()
        else:
            rect = self.visualRect(self.model().index(target_row, 0))
            mid = rect.top() + rect.height() // 2
            if pos.y() >= mid:
                target_row += 1

        # Adjust target if moving downwards because removing the source shifts everything up
        if target_row > source_row:
            target_row -= 1

        # Ignore at the Qt level to prevent the default QTableWidget item deletion,
        # but accept the event to stop propagation.
        event.setDropAction(Qt.IgnoreAction)
        event.accept()

        if source_row != target_row:
            self.reordered.emit(source_row, target_row)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._insert_line_y is not None:
            painter = QPainter(self.viewport())
            pen = QPen(QColor(255, 255, 255, 200), 2)
            painter.setPen(pen)
            w = self.viewport().width()
            painter.drawLine(0, self._insert_line_y, w, self._insert_line_y)
            painter.end()


class BatchQueueDock(QDockWidget):
    """A dock widget that holds the queue of configured sessions for batch transfer."""

    # Signals
    load_requested = Signal(object)  # Emits BatchItem
    run_batch_requested = Signal(list)  # Emits list[BatchItem]
    run_single_requested = Signal(object)  # Emits BatchItem

    def __init__(self, parent=None):
        super().__init__("Batch Queue", parent)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

        self._items: list[BatchItem] = []
        self._is_running = False

        self._build_ui()

    def _build_ui(self):
        container = QWidget()
        container.setMinimumWidth(450)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        # Table
        self._table = _BatchTable()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Project Name", "DAW", "Status", "Details"])
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.reordered.connect(self._on_table_reordered)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setVisible(False)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        layout.addWidget(self._table)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # Bottom bar
        bottom_layout = QHBoxLayout()
        self._status_label = QLabel("0 sessions queued")
        bottom_layout.addWidget(self._status_label)

        bottom_layout.addStretch()

        self._clear_btn = QPushButton("Clear All")
        self._clear_btn.clicked.connect(self.clear_all)
        bottom_layout.addWidget(self._clear_btn)

        self._run_btn = QPushButton("Run Batch")
        self._run_btn.clicked.connect(self._on_run_batch)
        self._run_btn.setStyleSheet(f"background-color: {COLORS['accent']}; color: white; font-weight: bold;")
        bottom_layout.addWidget(self._run_btn)

        layout.addLayout(bottom_layout)
        self.setWidget(container)

    def add_item(self, item: BatchItem) -> bool:
        """Add a job to the queue. Returns False if duplicate project name."""
        for existing in self._items:
            if existing.project_name.lower() == item.project_name.lower():
                QMessageBox.warning(self, "Duplicate Project Name", f"A session named '{item.project_name}' is already in the queue.")
                return False

        self._items.append(item)
        self._refresh_table()
        return True

    def remove_item(self, item_id: str):
        self._items = [i for i in self._items if i.id != item_id]
        self._refresh_table()

    def clear_all(self):
        self._items.clear()
        self._refresh_table()

    def update_item(self, item_id: str, status: str, result_text: str = ""):
        """Update the status and result text of a specific item."""
        for item in self._items:
            if item.id == item_id:
                item.status = status
                item.result_text = result_text
                break
        self._refresh_table()

    def get_pending_items(self) -> list[BatchItem]:
        return [i for i in self._items if i.status == "Pending" or i.status == "Failed"]

    def set_running_state(self, is_running: bool):
        self._is_running = is_running
        self._progress_bar.setVisible(is_running)
        if not is_running:
            self._progress_bar.setValue(0)
        self._clear_btn.setEnabled(not is_running)
        self._run_btn.setEnabled(not is_running and len(self.get_pending_items()) > 0)
        
    def update_progress(self, current: int, total: int):
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)

    def _refresh_table(self):
        self._table.setRowCount(0)
        for i, item in enumerate(self._items):
            self._table.insertRow(i)
            
            name_item = QTableWidgetItem(item.project_name)
            name_item.setData(Qt.UserRole, item.id)
            self._table.setItem(i, 0, name_item)
            
            self._table.setItem(i, 1, QTableWidgetItem(item.daw_processor_name))
            
            status_item = QTableWidgetItem(item.status)
            if item.status == "Success":
                status_item.setForeground(Qt.green) # Use a generic green, or from theme if needed
            elif item.status == "Failed":
                status_item.setForeground(Qt.red)
            elif item.status == "Running":
                status_item.setForeground(Qt.blue)
                
            self._table.setItem(i, 2, status_item)
            
            details_item = QTableWidgetItem(item.result_text)
            details_item.setToolTip(item.result_text)
            self._table.setItem(i, 3, details_item)

        pending_count = len(self.get_pending_items())
        self._status_label.setText(f"{len(self._items)} queued ({pending_count} pending)")
        
        if not self._is_running:
            self._run_btn.setEnabled(pending_count > 0)

    @Slot(int, int)
    def _on_table_reordered(self, source_row: int, target_row: int):
        if self._is_running:
            return  # Prevent reordering while batch is executing

        item = self._items.pop(source_row)
        self._items.insert(target_row, item)
        self._refresh_table()
        
        # Reselect the moved item
        self._table.selectRow(target_row)

    @Slot()
    def _on_run_batch(self):
        pending = self.get_pending_items()
        if pending:
            self.run_batch_requested.emit(pending)

    @Slot(object)
    def _on_context_menu(self, pos):
        if self._is_running:
            return
            
        row = self._table.rowAt(pos.y())
        if row < 0:
            return

        item_id = self._table.item(row, 0).data(Qt.UserRole)
        item = next((i for i in self._items if i.id == item_id), None)
        if not item:
            return

        menu = QMenu(self)

        load_act = menu.addAction("Load Session (Edit)")
        load_act.triggered.connect(lambda: self.load_requested.emit(item))

        run_act = menu.addAction("Run Individually")
        run_act.triggered.connect(lambda: self.run_single_requested.emit(item))

        menu.addSeparator()

        del_act = menu.addAction("Remove from Queue")
        del_act.triggered.connect(lambda: self.remove_item(item_id))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    @property
    def has_items(self) -> bool:
        return len(self._items) > 0
