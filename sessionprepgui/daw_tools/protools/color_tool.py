"""Color Picker tool for Pro Tools.

Shows the SessionPrep color palette; clicking a color pushes it
to the selected track(s) in Pro Tools via PTSL.
"""

from __future__ import annotations


from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from sessionpreplib.daw_processors import ptsl_helpers as ptslh

from ...widgets import ColorGridPanel


class ColorTool(QWidget):
    """Interactive color picker that pushes colors to Pro Tools."""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = config
        self._engine = None
        self._pt_palette: list[str] = []
        self._init_ui()
        self._load_palette()

    # ── UI ────────────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        desc = QLabel(
            "Click a color to apply it to the selected track(s) in Pro Tools. "
            "Colors are perceptually matched to the Pro Tools palette."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 9pt; margin-bottom: 6px;")
        layout.addWidget(desc)

        self._grid = ColorGridPanel(
            cell_height=28, stretch_vertical=True, parent=self)
        self._grid.colorClicked.connect(self._on_color_clicked)
        layout.addWidget(self._grid)

        # Status bar
        status_row = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet("color: #888; font-size: 8pt;")
        status_row.addWidget(self._status)
        status_row.addStretch()
        layout.addLayout(status_row)

    # ── Public API ───────────────────────────────────────────────────

    def set_engine(self, engine):
        """Set or clear the PTSL engine."""
        self._engine = engine
        self._pt_palette = []
        if engine is not None:
            self._fetch_pt_palette()

    def update_config(self, config: dict):
        """Refresh the palette grid from an updated config."""
        self._config = config
        self._load_palette()

    # ── Internal ─────────────────────────────────────────────────────

    def _load_palette(self):
        """Load the SessionPrep palette from config into the grid."""
        colors = self._config.get("colors", [])
        self._grid.set_colors(colors)

    def _fetch_pt_palette(self):
        """Fetch the Pro Tools track color palette via PTSL."""
        if self._engine is None:
            return
        try:
            resp = ptslh.run_command(
                self._engine, "CId_GetColorPalette",
                {"color_palette_target": "CPTarget_Tracks"})
            self._pt_palette = (resp or {}).get("color_list", [])
            count = len(self._pt_palette)
            if count:
                self._status.setText(f"PT palette loaded ({count} colors)")
                self._status.setStyleSheet("color: #4caf50; font-size: 8pt;")
            else:
                self._status.setText(
                    f"PT palette empty (response: {resp})")
                self._status.setStyleSheet("color: #ff9800; font-size: 8pt;")
        except Exception as e:
            self._status.setText(f"Failed to fetch PT palette: {e}")
            self._status.setStyleSheet("color: #f44336; font-size: 8pt;")

    def _on_color_clicked(self, index: int):
        """Handle a palette cell click — push color to Pro Tools."""
        if self._engine is None:
            self._status.setText("Not connected to Pro Tools")
            self._status.setStyleSheet("color: #f44336; font-size: 8pt;")
            return

        colors = self._config.get("colors", [])
        if index < 0 or index >= len(colors):
            return

        entry = colors[index]
        argb = entry.get("argb", "")
        name = entry.get("name", "")
        if not argb:
            return

        # Fetch PT palette if not cached
        if not self._pt_palette:
            self._fetch_pt_palette()
        if not self._pt_palette:
            self._status.setText("No PT palette available")
            self._status.setStyleSheet("color: #f44336; font-size: 8pt;")
            return

        # Find closest PT palette match (0-based → 1-based for PT)
        pt_index = ptslh.closest_palette_index(argb, self._pt_palette)
        if pt_index is None:
            self._status.setText("Could not match color")
            self._status.setStyleSheet("color: #f44336; font-size: 8pt;")
            return

        # Apply to selected tracks
        try:
            selected = ptslh.get_selected_track_names(self._engine)
            if not selected:
                self._status.setText("No tracks selected in Pro Tools")
                self._status.setStyleSheet("color: #ff9800; font-size: 8pt;")
                return
            ptslh.set_track_color(
                self._engine, color_index=pt_index + 1,
                track_names=selected)
            label = name or argb
            self._status.setText(
                f"Applied '{label}' → PT index {pt_index} "
                f"({len(selected)} track{'s' if len(selected) != 1 else ''})")
            self._status.setStyleSheet("color: #4caf50; font-size: 8pt;")
        except Exception as e:
            self._status.setText(f"Error: {e}")
            self._status.setStyleSheet("color: #f44336; font-size: 8pt;")
