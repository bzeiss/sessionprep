from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import uuid

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
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
    output_path: str
    session_state: dict[str, Any]
    status: str = "Pending"
    result_text: str = ""


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

        self._build_ui()

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Project Name", "DAW", "Status", "Details"])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setVisible(False)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        layout.addWidget(self._table)

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

    def _refresh_table(self):
        self._table.setRowCount(0)
        for i, item in enumerate(self._items):
            self._table.insertRow(i)
            
            name_item = QTableWidgetItem(item.project_name)
            name_item.setData(Qt.UserRole, item.id)
            self._table.setItem(i, 0, name_item)
            
            self._table.setItem(i, 1, QTableWidgetItem(item.daw_processor_id))
            
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
        self._run_btn.setEnabled(pending_count > 0)

    @Slot()
    def _on_run_batch(self):
        pending = self.get_pending_items()
        if pending:
            self.run_batch_requested.emit(pending)

    @Slot(object)
    def _on_context_menu(self, pos):
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
