"""Pro Tools Utils — standalone utility window.

Hosts per-tool tabs and manages a shared PTSL engine connection.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from .color_tool import ColorTool


class ProToolsUtilsWindow(QDialog):
    """Detached utility window for Pro Tools interactive tools."""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pro Tools Utils")
        self.setMinimumSize(600, 300)
        self.setAttribute(Qt.WA_DeleteOnClose, False)  # reuse window

        self._config = config
        self._engine = None

        self._init_ui()

    # ── UI ────────────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Connection header
        header = QHBoxLayout()
        header.setSpacing(8)

        self._status_label = QLabel("Disconnected")
        self._status_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        header.addWidget(self._status_label)

        header.addStretch()

        self._on_top_cb = QCheckBox("Always on Top")
        self._on_top_cb.setStyleSheet("color: #aaa; font-size: 8pt;")
        self._on_top_cb.toggled.connect(self._toggle_on_top)
        header.addWidget(self._on_top_cb)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._toggle_connection)
        header.addWidget(self._connect_btn)

        layout.addLayout(header)

        # Tab widget for tools
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs, 1)

        # Register tools
        self._color_tool = ColorTool(self._config, self)
        self._tabs.addTab(self._color_tool, "Color Picker")

    # ── Connection management ────────────────────────────────────────

    def _toggle_on_top(self, checked: bool):
        geo = self.geometry()
        was_visible = self.isVisible()
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        # Ensure standard title-bar buttons survive the flag change
        flags |= Qt.WindowCloseButtonHint | Qt.WindowMinMaxButtonsHint
        self.setWindowFlags(flags)
        if was_visible:
            self.setGeometry(geo)
            self.show()

    def _toggle_connection(self):
        if self._engine is not None:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        try:
            from ptsl import Engine
            self._engine = Engine(
                company_name="SessionPrep",
                application_name="Pro Tools Utils",
            )
            self._status_label.setText("Connected")
            self._status_label.setStyleSheet("color: #4caf50; font-size: 9pt;")
            self._connect_btn.setText("Disconnect")
            self._color_tool.set_engine(self._engine)
        except Exception as e:
            self._status_label.setText(f"Connection failed: {e}")
            self._status_label.setStyleSheet("color: #f44336; font-size: 9pt;")
            self._engine = None

    def _disconnect(self):
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:
                pass
            self._engine = None
        self._status_label.setText("Disconnected")
        self._status_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        self._connect_btn.setText("Connect")
        self._color_tool.set_engine(None)

    def update_config(self, config: dict):
        """Update the config (e.g. after preferences change)."""
        self._config = config
        self._color_tool.update_config(config)

    def showEvent(self, event):
        super().showEvent(event)
        if self._engine is None:
            self._connect()

    def closeEvent(self, event):
        self._disconnect()
        super().closeEvent(event)
