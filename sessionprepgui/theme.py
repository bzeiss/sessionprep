"""Color palette, constants, and dark-theme application."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# ---------------------------------------------------------------------------
# Color palette (matching CLI categories)
# ---------------------------------------------------------------------------

COLORS = {
    "problems": "#ff4444",
    "attention": "#ffaa00",
    "information": "#4499ff",
    "clean": "#44cc44",
    "hints": "#44cccc",
    "dim": "#888888",
    "text": "#dddddd",
    "heading": "#ffffff",
    "bg": "#1e1e1e",
    "bg_alt": "#252525",
    "accent": "#3a3a3a",
}

# File list item colors by status
FILE_COLOR_OK = QColor("#cccccc")
FILE_COLOR_ERROR = QColor("#ff4444")
FILE_COLOR_SILENT = QColor("#888888")
FILE_COLOR_TRANSIENT = QColor("#cc77ff")
FILE_COLOR_SUSTAINED = QColor("#44cccc")


# ---------------------------------------------------------------------------
# Dark theme
# ---------------------------------------------------------------------------

STYLESHEET = """
    QMainWindow { background-color: #1e1e1e; }
    QMenuBar { background-color: #252525; color: #dddddd; }
    QMenuBar::item:selected { background-color: #3a3a3a; }
    QMenu { background-color: #2d2d2d; color: #dddddd; border: 1px solid #555; }
    QMenu::item:selected { background-color: #2a6db5; }
    QToolBar { background-color: #2d2d2d; border-bottom: 1px solid #555; spacing: 6px; padding: 2px; }
    QToolBar QToolButton { color: #dddddd; padding: 4px 8px; }
    QToolBar QToolButton:hover { background-color: #3a3a3a; }
    QToolBar QToolButton:disabled { color: #666666; }
    QSplitter::handle { background-color: #555; width: 2px; }
    QTableWidget { background-color: #252525; border: none; outline: none; gridline-color: #3a3a3a; }
    QTableWidget::item { padding: 2px 6px; }
    QTableWidget::item:selected { background-color: #2a6db5; }
    QHeaderView::section { background-color: #2d2d2d; color: #dddddd; border: none; border-bottom: 1px solid #555; padding: 4px 6px; }
    QTextBrowser { background-color: #1e1e1e; border: none; }
    QPushButton { background-color: #3a3a3a; color: #dddddd; border: 1px solid #555; padding: 4px 12px; border-radius: 2px; }
    QPushButton:hover { background-color: #4a4a4a; }
    QPushButton:pressed { background-color: #2a6db5; }
    QPushButton:disabled { color: #666666; background-color: #2d2d2d; }
    QStatusBar { background-color: #2d2d2d; color: #888888; }
    QTabWidget::pane { border-top: 1px solid #555; background-color: #1e1e1e; }
    QTabBar { background-color: #2d2d2d; qproperty-drawBase: 0; }
    QTabBar::tab {
        background-color: #2d2d2d; color: #aaaaaa;
        border: none; border-bottom: 2px solid transparent;
        padding: 6px 16px; margin-right: 2px;
    }
    QTabBar::tab:selected { color: #dddddd; border-bottom: 2px solid #4499ff; }
    QTabBar::tab:hover:!selected { color: #cccccc; background-color: #353535; }
    QTabBar::tab:disabled { color: #555555; }
    /* Top-level phase tabs (documentMode stretches tab bar to full width) */
    QTabWidget#phaseTabs > QTabBar {
        background-color: #343434;
        border: none;
        qproperty-drawBase: 0;
    }
    QTabWidget#phaseTabs > QTabBar::tab {
        background-color: #343434; color: #aaaaaa;
        padding: 8px 24px; font-size: 10pt;
        border: none; border-bottom: 2px solid transparent;
    }
    QTabWidget#phaseTabs > QTabBar::tab:selected {
        color: #dddddd; border-bottom: 2px solid #4499ff;
    }
    QTabWidget#phaseTabs > QTabBar::tab:hover:!selected {
        color: #cccccc; background-color: #3d3d3d;
    }
    QProgressBar {
        background-color: #2d2d2d; border: 1px solid #555; border-radius: 4px;
        text-align: center; color: #dddddd; height: 20px;
    }
    QProgressBar::chunk { background-color: #2a6db5; border-radius: 3px; }
"""


def apply_dark_theme(window) -> None:
    """Apply the dark palette and stylesheet to the application and window."""
    app = QApplication.instance()

    palette = QPalette()
    bg = QColor(COLORS["bg"])
    bg_alt = QColor(COLORS["bg_alt"])
    accent = QColor(COLORS["accent"])
    text = QColor(COLORS["text"])
    highlight = QColor("#2a6db5")

    palette.setColor(QPalette.Window, bg)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, bg_alt)
    palette.setColor(QPalette.AlternateBase, accent)
    palette.setColor(QPalette.ToolTipBase, bg_alt)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, accent)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Link, QColor(COLORS["information"]))
    palette.setColor(QPalette.Highlight, highlight)
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))

    # Disabled state
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#666666"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#666666"))

    app.setPalette(palette)
    window.setStyleSheet(STYLESHEET)
