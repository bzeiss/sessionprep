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
# Default color palette (69 colors, ARGB format)
# ---------------------------------------------------------------------------
# Organized in three saturation tiers (bright → medium → dark) with hue
# rotation within each tier.  Names are descriptive for human readability.

PT_DEFAULT_COLORS: list[dict[str, str]] = [
    # ── Bright tier (indices 0–22) ─────────────────────────────────────────
    {"name": "Blue Dark",                   "argb": "#ff2c00fc"},
    {"name": "Electric Violet Dark",        "argb": "#ff5600fc"},
    {"name": "Electric Violet",             "argb": "#ff8800fc"},
    {"name": "Electric Violet Lightest",    "argb": "#ffbf00fc"},
    {"name": "Electric Violet Light",       "argb": "#ffbe00c0"},
    {"name": "Flirt",                       "argb": "#ffbd0088"},
    {"name": "Lipstick",                    "argb": "#ffbd0054"},
    {"name": "Guardsman Red",               "argb": "#ffbc000d"},
    {"name": "Milano Red",                  "argb": "#ffbd1e0d"},
    {"name": "Tia Maria",                   "argb": "#ffbd520e"},
    {"name": "Pizza",                       "argb": "#ffbe8911"},
    {"name": "La Rioja",                    "argb": "#ffc0c514"},
    {"name": "Lima Light",                  "argb": "#ff89c511"},
    {"name": "Lima",                        "argb": "#ff57c610"},
    {"name": "Christi",                     "argb": "#ff2ec60f"},
    {"name": "Malachite",                   "argb": "#ff1cc60e"},
    {"name": "Mountain Meadow",             "argb": "#ff1ec654"},
    {"name": "Mountain Meadow Light",       "argb": "#ff20c488"},
    {"name": "Java",                        "argb": "#ff23c3c1"},
    {"name": "Dodger Blue Light",           "argb": "#ff27c1fd"},
    {"name": "Dodger Blue",                 "argb": "#ff2184fc"},
    {"name": "Blue Ribbon",                 "argb": "#ff1c4afc"},
    {"name": "Blue Light",                  "argb": "#ff1900fc"},
    # ── Medium tier (indices 23–45) ────────────────────────────────────────
    {"name": "Navy Blue",                   "argb": "#ff1e00a3"},
    {"name": "Pigment Indigo",              "argb": "#ff3700a3"},
    {"name": "Purple Dark",                 "argb": "#ff5500a3"},
    {"name": "Purple",                      "argb": "#ff7400a4"},
    {"name": "Purple Light",                "argb": "#ff7c0089"},
    {"name": "Cardinal Pink",               "argb": "#ff7b0066"},
    {"name": "Siren",                       "argb": "#ff7a0046"},
    {"name": "Japanese Maple",              "argb": "#ff7a000b"},
    {"name": "Dark Burgundy",               "argb": "#ff7a120b"},
    {"name": "Cafe Royale Dark",            "argb": "#ff7a310c"},
    {"name": "Cafe Royale",                 "argb": "#ff7b510d"},
    {"name": "Corn Harvest",                "argb": "#ff898010"},
    {"name": "Olivetone",                   "argb": "#ff66800e"},
    {"name": "Green Leaf",                  "argb": "#ff48800d"},
    {"name": "Bilbao",                      "argb": "#ff2d800c"},
    {"name": "Japanese Laurel",             "argb": "#ff18800c"},
    {"name": "Jewel Dark",                  "argb": "#ff158033"},
    {"name": "Jewel",                       "argb": "#ff167f51"},
    {"name": "Elm",                         "argb": "#ff1a8c7e"},
    {"name": "Eastern Blue",                "argb": "#ff1d8da4"},
    {"name": "Matisse",                     "argb": "#ff1969a4"},
    {"name": "Tory Blue",                   "argb": "#ff1646a3"},
    {"name": "Torea Bay",                   "argb": "#ff1423a3"},
    # ── Dark tier (indices 46–65) ──────────────────────────────────────────
    {"name": "Paua Dark",                   "argb": "#ff14005f"},
    {"name": "Paua",                        "argb": "#ff21005f"},
    {"name": "Ripe Plum Dark",              "argb": "#ff31005f"},
    {"name": "Ripe Plum",                   "argb": "#ff41005f"},
    {"name": "Ripe Plum Light",             "argb": "#ff4b0057"},
    {"name": "Blackberry",                  "argb": "#ff470042"},
    {"name": "Barossa",                     "argb": "#ff470031"},
    {"name": "Temptress",                   "argb": "#ff47000b"},
    {"name": "Van Cleef Dark",              "argb": "#ff470c0b"},
    {"name": "Van Cleef",                   "argb": "#ff471c0b"},
    {"name": "Bronze",                      "argb": "#ff472c0c"},
    {"name": "Saratoga",                    "argb": "#ff574d0f"},
    {"name": "Bronze Olive",                "argb": "#ff424a0c"},
    {"name": "Green House Dark",            "argb": "#ff324a0c"},
    {"name": "Green House",                 "argb": "#ff234a0c"},
    {"name": "Dark Fern",                   "argb": "#ff154b0b"},
    {"name": "Parsley",                     "argb": "#ff0f4a1d"},
    {"name": "Bottle Green",                "argb": "#ff0f4a2c"},
    {"name": "Eden Darkest",                "argb": "#ff14594c"},
    {"name": "Eden Light",                  "argb": "#ff16595f"},
    {"name": "Eden",                        "argb": "#ff14475f"},
    {"name": "Blue Zodiac Light",           "argb": "#ff13355f"},
    {"name": "Blue Zodiac",                 "argb": "#ff11225f"},
]


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
    QToolBar QToolButton { color: #dddddd; padding: 4px 8px; background: transparent; border: none; }
    QToolBar QToolButton:hover { background-color: #3a3a3a; }
    QToolBar QToolButton:disabled { color: #666666; }
    QToolBar QPushButton { color: #dddddd; padding: 4px 8px; background: transparent; border: none; border-radius: 0; }
    QToolBar QPushButton:hover { background-color: #3a3a3a; }
    QToolBar QPushButton:disabled { color: #666666; background: transparent; }
    QSplitter::handle { background-color: #555; width: 2px; }
    QTableWidget { background-color: #252525; border: none; outline: none; gridline-color: #3a3a3a; }
    QTableWidget::item { padding: 2px 6px; }
    QTreeWidget { background-color: #252525; border: none; outline: none; }
    QTreeWidget::item { padding: 3px 4px; }
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
