"""Track table mixin: population, column widgets, batch ops, row helpers."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHeaderView,
    QMenu,
    QToolButton,
)

from sessionpreplib.detector import TrackDetector
from sessionpreplib.processors import default_processors
from sessionpreplib.utils import protools_sort_key

from ..helpers import track_analysis_label
from ..detail.report import render_track_detail_html
from .table_widgets import _SortableItem, _make_analysis_cell
from ..theme import (
    COLORS,
    FILE_COLOR_OK,
    FILE_COLOR_ERROR,
    FILE_COLOR_SILENT,
    FILE_COLOR_TRANSIENT,
    FILE_COLOR_SUSTAINED,
)
from ..widgets import BatchComboBox, BatchToolButton
from ..analysis.worker import BatchReanalyzeWorker


class TrackColumnsMixin:
    """Track table population, column widgets, batch operations, row helpers.

    Mixed into ``SessionPrepWindow`` — not meant to be used standalone.
    """

    # ── Track selection ────────────────────────────────────────────────

    @Slot(int, int)
    def _on_row_clicked(self, row, _column):
        self._select_row(row)

    @Slot(int, int, int, int)
    def _on_current_cell_changed(self, row, _col, _prev_row, _prev_col):
        self._select_row(row)

    def _select_row(self, row: int):
        if not self._session or row < 0:
            return
        fname_item = self._track_table.item(row, 0)
        if not fname_item:
            return
        fname = fname_item.text()
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return
        self._show_track_detail(track)

    # ── Row lookup ────────────────────────────────────────────────────────

    def _find_table_row(self, filename: str) -> int:
        """Return the table row index for *filename*, or -1 if not found."""
        for row in range(self._track_table.rowCount()):
            item = self._track_table.item(row, 0)
            if item and item.text() == filename:
                return row
        return -1

    # ── Table population ─────────────────────────────────────────────────

    def _populate_table(self, session):
        """Update the track table with analysis results."""
        self._track_table.setSortingEnabled(False)
        track_map = {t.filename: t for t in session.tracks}
        for row in range(self._track_table.rowCount()):
            # Remove any previous cell widgets before repopulating
            self._track_table.removeCellWidget(row, 3)
            self._track_table.removeCellWidget(row, 4)
            self._track_table.removeCellWidget(row, 5)
            self._track_table.removeCellWidget(row, 6)
            self._track_table.removeCellWidget(row, 7)

            fname_item = self._track_table.item(row, 0)
            if not fname_item:
                continue
            track = track_map.get(fname_item.text())
            if not track:
                continue

            # Column 1: channel count
            ch_item = _SortableItem(str(track.channels), track.channels)
            ch_item.setForeground(QColor(COLORS["dim"]))
            self._track_table.setItem(row, 1, ch_item)

            # Column 2: severity counts
            dets = session.detectors if hasattr(session, 'detectors') else None
            _plain, html, _color, sort_key = track_analysis_label(track, dets)
            lbl, item = _make_analysis_cell(html, sort_key)
            self._track_table.setItem(row, 2, item)
            self._track_table.setCellWidget(row, 2, lbl)

            # Column 3: classification (combo or static)
            # Column 4: gain (spin box or static)
            pr = (
                next(iter(track.processor_results.values()), None)
                if track.processor_results
                else None
            )
            if track.status != "OK":
                cls_item = _SortableItem("Error", "error")
                cls_item.setForeground(FILE_COLOR_ERROR)
                self._track_table.setItem(row, 3, cls_item)
                gain_item = _SortableItem("", 0.0)
                gain_item.setForeground(QColor(COLORS["dim"]))
                self._track_table.setItem(row, 4, gain_item)
            elif pr and pr.classification == "Silent":
                cls_item = _SortableItem("Silent", "silent")
                cls_item.setForeground(FILE_COLOR_SILENT)
                self._track_table.setItem(row, 3, cls_item)
                gain_item = _SortableItem("0.0 dB", 0.0)
                gain_item.setForeground(QColor(COLORS["dim"]))
                self._track_table.setItem(row, 4, gain_item)
            elif pr:
                # Determine effective classification
                cls_text = pr.classification or "Unknown"
                if "Transient" in cls_text:
                    base_cls = "Transient"
                elif cls_text == "Skip":
                    base_cls = "Skip"
                elif "Sustained" in cls_text:
                    base_cls = "Sustained"
                else:
                    base_cls = "Sustained"

                # Hidden sort item (widget overlays it)
                sort_item = _SortableItem(base_cls, base_cls.lower())
                self._track_table.setItem(row, 3, sort_item)

                # Classification combo widget
                combo = BatchComboBox()
                combo.addItems(["Transient", "Sustained", "Skip"])
                combo.blockSignals(True)
                combo.setCurrentText(base_cls)
                combo.blockSignals(False)
                combo.setProperty("track_filename", track.filename)
                self._style_classification_combo(combo, base_cls)
                combo.textActivated.connect(
                    lambda text, c=combo: self._on_classification_changed(text, c))
                self._track_table.setCellWidget(row, 3, combo)

                # Gain spin box
                gain_db = pr.gain_db
                gain_sort = _SortableItem(f"{gain_db:+.1f}", gain_db)
                self._track_table.setItem(row, 4, gain_sort)

                spin = QDoubleSpinBox()
                spin.setRange(-60.0, 60.0)
                spin.setSingleStep(0.1)
                spin.setDecimals(1)
                spin.setSuffix(" dB")
                spin.blockSignals(True)
                spin.setValue(gain_db)
                spin.blockSignals(False)
                spin.setProperty("track_filename", track.filename)
                spin.setEnabled(base_cls != "Skip")
                spin.setStyleSheet(
                    f"QDoubleSpinBox {{ color: {COLORS['text']}; }}"
                )
                spin.valueChanged.connect(
                    lambda value, s=spin: self._on_gain_changed(value, s))
                self._track_table.setCellWidget(row, 4, spin)

                # RMS Anchor combo (column 5)
                self._create_anchor_combo(row, track)
            elif track.status == "OK":
                # OK track but no processor results (all processors disabled)
                cls_item = _SortableItem("", "zzz")
                self._track_table.setItem(row, 3, cls_item)
                gain_item = _SortableItem("", 0.0)
                self._track_table.setItem(row, 4, gain_item)
            else:
                cls_item = _SortableItem("", "zzz")
                self._track_table.setItem(row, 3, cls_item)
                gain_item = _SortableItem("", 0.0)
                self._track_table.setItem(row, 4, gain_item)

            # Group combo, processing button, and row color for all OK tracks
            if track.status == "OK":
                # Group combo (column 6)
                self._create_group_combo(row, track)

                # Processing multiselect (column 7)
                self._create_processing_button(row, track)

                # Row background from group color
                self._apply_row_group_color(row, track.group)
        self._track_table.setSortingEnabled(True)

        # Auto-fit columns 2–7 to content, File column stays Stretch, Ch stays Fixed
        header = self._track_table.horizontalHeader()
        for col in (2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._track_table.resizeColumnsToContents()
        for col in (2, 3, 4, 5, 6, 7):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        self._auto_fit_group_column()
        self._auto_fit_track_table()

    def _populate_setup_table(self):
        """Refresh the Session Setup track table from the transfer manifest."""
        if not self._session:
            return
        self._setup_table.setSortingEnabled(False)
        self._setup_table.setRowCount(0)

        manifest = self._session.transfer_manifest
        if not manifest:
            return

        # Build lookup: output_filename → TrackContext from output_tracks
        out_map: dict[str, Any] = {
            t.filename: t for t in self._session.output_tracks
        }

        self._setup_table.setRowCount(len(manifest))
        gcm = self._group_color_map()
        gcm_rank = self._group_rank_map()
        glm = self._gain_linked_map()

        # Determine which entries are assigned to a DAW folder
        assignments = {}
        if self._session.daw_state and self._active_daw_processor:
            dp_state = self._session.daw_state.get(
                self._active_daw_processor.id, {})
            assignments = dp_state.get("assignments", {})

        for row, entry in enumerate(manifest):
            track = out_map.get(entry.output_filename)
            pr = (
                next(iter(track.processor_results.values()), None)
                if track and track.processor_results
                else None
            )
            # Column 0: checkmark (assigned to folder?)
            assigned = entry.entry_id in assignments
            chk_item = _SortableItem("✓" if assigned else "", int(not assigned))
            if assigned:
                chk_item.setForeground(QColor(COLORS["clean"]))
            self._setup_table.setItem(row, 0, chk_item)

            # Column 1: filename (output_filename from manifest)
            fname_item = _SortableItem(
                entry.output_filename,
                protools_sort_key(entry.output_filename))
            fname_item.setForeground(FILE_COLOR_OK)
            # Store entry_id in UserRole for drag-drop and assignment lookups
            fname_item.setData(Qt.UserRole, entry.entry_id)
            self._setup_table.setItem(row, 1, fname_item)

            # Column 2: channels
            channels = track.channels if track else 0
            ch_item = _SortableItem(str(channels), channels)
            ch_item.setForeground(QColor(COLORS["dim"]))
            self._setup_table.setItem(row, 2, ch_item)

            # Column 3: clip gain
            clip_gain = pr.gain_db if pr else 0.0
            cg_item = _SortableItem(f"{clip_gain:+.1f} dB", clip_gain)
            cg_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 3, cg_item)

            # Column 4: fader gain
            fader_gain = pr.data.get("fader_offset", 0.0) if pr else 0.0
            fg_item = _SortableItem(f"{fader_gain:+.1f} dB", fader_gain)
            fg_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 4, fg_item)

            # Column 5: group (read-only, with link indicator)
            grp = entry.group
            grp_label = self._group_display_name(grp, glm) if grp else ""
            grp_rank = gcm_rank.get(grp, len(gcm_rank)) if grp else len(gcm_rank)
            grp_item = _SortableItem(grp_label, grp_rank)
            grp_item.setForeground(QColor(COLORS["text"]))
            self._setup_table.setItem(row, 5, grp_item)

            # Row background from group color
            self._apply_row_group_color(row, grp, gcm,
                                        table=self._setup_table)

        self._setup_table.setSortingEnabled(True)

        # Auto-fit columns to content
        sh = self._setup_table.horizontalHeader()
        for col in range(self._setup_table.columnCount()):
            sh.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._setup_table.resizeColumnsToContents()
        sh.setSectionResizeMode(0, QHeaderView.Fixed)
        sh.resizeSection(0, 24)
        sh.setSectionResizeMode(1, QHeaderView.Stretch)
        sh.setSectionResizeMode(2, QHeaderView.Fixed)
        for col in range(3, self._setup_table.columnCount()):
            sh.setSectionResizeMode(col, QHeaderView.Interactive)

    # ── Classification override helpers ───────────────────────────────────

    def _style_classification_combo(self, combo: QComboBox, cls_text: str):
        """Apply classification-specific color to a combo box."""
        if cls_text == "Transient":
            color = FILE_COLOR_TRANSIENT.name()
        elif cls_text == "Sustained":
            color = FILE_COLOR_SUSTAINED.name()
        else:
            color = FILE_COLOR_SILENT.name()
        combo.setStyleSheet(f"QComboBox {{ color: {color}; font-weight: bold; }}")

    def _on_classification_changed(self, text: str, combo=None):
        """Handle user changing the classification dropdown."""
        if combo is None:
            combo = self.sender()
        if not combo or not self._session:
            return
        fname = combo.property("track_filename")
        if not fname:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return
        if getattr(combo, 'batch_mode', False) or combo.property("_batch_mode"):
            combo.setProperty("_batch_mode", False)
            combo.batch_mode = False
            track.classification_override = text
            def _prepare(t):
                t.classification_override = text
            self._batch_apply_combo(combo, 3, text, _prepare,
                                    run_detectors=False)
        else:
            # Skip if the value didn't actually change
            if track.classification_override == text:
                return
            track.classification_override = text
            # Single-track sync path
            self._recalculate_processor(track)
            self._style_classification_combo(combo, text)
            self._update_track_row(fname)
            self._refresh_file_tab(track)
        self._mark_prepare_stale()

    def _on_gain_changed(self, value: float, spin=None):
        """Handle user manually editing the gain spin box."""
        if spin is None:
            spin = self.sender()
        if not spin or not self._session:
            return
        fname = spin.property("track_filename")
        if not fname:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return

        # Write gain directly to the processor result
        pr = next(iter(track.processor_results.values()), None)
        if pr:
            pr.gain_db = value
        self._mark_prepare_stale()

        # Update hidden sort item
        for row in range(self._track_table.rowCount()):
            item = self._track_table.item(row, 0)
            if item and item.text() == fname:
                gain_sort = self._track_table.item(row, 4)
                if gain_sort:
                    gain_sort.setText(f"{value:+.1f}")
                    gain_sort._sort_key = value
                break

        # Refresh File tab if this track is currently displayed
        if self._current_track and self._current_track.filename == fname:
            html = render_track_detail_html(track, self._session,
                                            show_clean=self._show_clean,
                                            verbose=self._verbose)
            self._file_report.setHtml(self._wrap_html(html))

    # ── RMS Anchor override helpers ──────────────────────────────────────

    _ANCHOR_LABELS = ["Default", "Max", "P99", "P95", "P90", "P85"]
    _ANCHOR_TO_OVERRIDE = {
        "Default": None, "Max": "max",
        "P99": "p99", "P95": "p95", "P90": "p90", "P85": "p85",
    }
    _OVERRIDE_TO_LABEL = {v: k for k, v in _ANCHOR_TO_OVERRIDE.items()}

    def _create_anchor_combo(self, row: int, track):
        """Create and install an RMS Anchor combo in column 5."""
        anchor_sort = _SortableItem("Default", "default")
        self._track_table.setItem(row, 5, anchor_sort)

        combo = BatchComboBox()
        combo.addItems(self._ANCHOR_LABELS)
        combo.blockSignals(True)
        current = self._OVERRIDE_TO_LABEL.get(
            track.rms_anchor_override, "Default")
        combo.setCurrentText(current)
        combo.blockSignals(False)
        combo.setProperty("track_filename", track.filename)
        combo.setStyleSheet(
            f"QComboBox {{ color: {COLORS['text']}; }}"
        )
        combo.textActivated.connect(
            lambda text, c=combo: self._on_rms_anchor_changed(text, c))
        self._track_table.setCellWidget(row, 5, combo)

    def _on_rms_anchor_changed(self, text: str, combo=None):
        """Handle user changing the RMS Anchor dropdown."""
        if combo is None:
            combo = self.sender()
        if not combo or not self._session:
            return
        fname = combo.property("track_filename")
        if not fname:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return

        new_override = self._ANCHOR_TO_OVERRIDE.get(text)

        # Batch path: async re-analysis for all selected rows
        if combo.property("_batch_mode"):
            combo.setProperty("_batch_mode", False)
            combo.batch_mode = False
            track.rms_anchor_override = new_override
            def _prepare(t):
                t.rms_anchor_override = new_override
            self._batch_apply_combo(combo, 5, text, _prepare,
                                    run_detectors=True)
        else:
            # Skip if the value didn't actually change (textActivated
            # fires even when the user re-selects the same item)
            if track.rms_anchor_override == new_override:
                return
            track.rms_anchor_override = new_override
            self._reanalyze_single_track(track)
        self._mark_prepare_stale()

    # ── Processing column (col 7) ──────────────────────────────────────

    def _create_processing_button(self, row: int, track) -> None:
        """Create a multiselect tool button for the Processing column."""
        if track.status != "OK":
            item = _SortableItem("", "zzz")
            self._track_table.setItem(row, 7, item)
            return

        processors = self._session.processors if self._session else []

        btn = BatchToolButton()
        btn.setProperty("track_filename", track.filename)

        if processors:
            btn.setPopupMode(QToolButton.InstantPopup)
            menu = QMenu(btn)
            for proc in processors:
                action = menu.addAction(proc.name)
                action.setCheckable(True)
                checked = proc.id not in track.processor_skip
                action.setChecked(checked)
                action.setData(proc.id)
                action.toggled.connect(
                    lambda checked, a=action: self._on_processing_toggled(checked, a))
            btn.setMenu(menu)
        else:
            btn.setEnabled(False)

        self._update_processing_button_label(btn, track, processors)

        # Hidden sort item
        sort_item = _SortableItem("", len(track.processor_skip))
        self._track_table.setItem(row, 7, sort_item)
        self._track_table.setCellWidget(row, 7, btn)

    def _update_processing_button_label(self, btn, track, processors):
        """Set the button label based on current processor_skip state."""
        if not processors:
            btn.setText("None")
            btn.setToolTip("No audio processors enabled")
            return
        def _label(p):
            return p.shorthand if p.shorthand else p.name

        active = [p for p in processors if p.id not in track.processor_skip]
        active_labels = [_label(p) for p in active]
        active_names = [p.name for p in active]
        # "Default" means the current selection matches each processor's
        # configured default (default=True → active, default=False → skipped).
        is_default = all(
            (p.id not in track.processor_skip) == p.default
            for p in processors
        )
        if is_default:
            default_active_names = [p.name for p in processors if p.default]
            if default_active_names:
                btn.setText("Default")
                btn.setToolTip("Default selection: " + ", ".join(default_active_names))
            else:
                btn.setText("Default")
                btn.setToolTip("Default: all processors deselected")
        elif not active:
            btn.setText("None")
            btn.setToolTip("All processors skipped for this track")
        else:
            btn.setText(", ".join(active_labels))
            btn.setToolTip("Active processors: " + ", ".join(active_names))

    def _on_processing_toggled(self, checked: bool, action=None):
        """Handle user toggling a processor in the Processing column menu."""
        if action is None:
            action = self.sender()
        if not action:
            return
        menu = action.parent()
        if not menu:
            return
        btn = menu.parent()
        if not btn:
            return
        fname = btn.property("track_filename")
        if not fname or not self._session:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == fname), None
        )
        if not track:
            return

        proc_id = action.data()
        processors = self._session.processors if self._session else []

        if btn.property("_batch_mode"):
            btn.setProperty("_batch_mode", False)
            btn.batch_mode = False
            batch_keys = self._track_table.batch_selected_keys()
            track_map = {t.filename: t for t in self._session.tracks}
            for fname in batch_keys:
                t = track_map.get(fname)
                if not t or t.status != "OK":
                    continue
                if checked:
                    t.processor_skip.discard(proc_id)
                else:
                    t.processor_skip.add(proc_id)
                row = self._find_table_row(fname)
                if row >= 0:
                    b = self._track_table.cellWidget(row, 7)
                    if b:
                        self._update_processing_button_label(b, t, processors)
            self._track_table.restore_selection(batch_keys)
        else:
            if checked:
                track.processor_skip.discard(proc_id)
            else:
                track.processor_skip.add(proc_id)
            self._update_processing_button_label(btn, track, processors)

        self._mark_prepare_stale()

    # ── Batch combo helper ────────────────────────────────────────────────

    def _batch_apply_combo(self, source_combo, column: int, value: str,
                           prepare_fn, run_detectors: bool = True):
        """Apply *value* to the combo in *column* for every selected row.

        1. **Sync** — set overrides via *prepare_fn(track)* and update
           combo widgets instantly.
        2. **Async** — start a ``BatchReanalyzeWorker`` that re-runs
           detectors/processors in the background, updating table rows
           as each track completes and restoring the multi-selection at
           the end.

        *prepare_fn(track)* must only mutate the data model (e.g. set an
        override field).  It must **not** run analysis.
        """
        if not self._session:
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        if self._worker and self._worker.isRunning():
            return

        track_map = {t.filename: t for t in self._session.tracks}
        batch_keys = self._track_table.batch_selected_keys()

        # Collect tracks and update combo widgets (sync, instant)
        tracks_to_reanalyze: list = []
        self._track_table.setSortingEnabled(False)
        for fname in batch_keys:
            track = track_map.get(fname)
            if not track or track.status != "OK":
                continue
            prepare_fn(track)
            tracks_to_reanalyze.append(track)
            row = self._find_table_row(fname)
            if row >= 0:
                w = self._track_table.cellWidget(row, column)
                if isinstance(w, BatchComboBox):
                    w.blockSignals(True)
                    w.setCurrentText(value)
                    w.blockSignals(False)
        if not tracks_to_reanalyze:
            self._track_table.setSortingEnabled(True)
            return

        # Save filenames for selection restore after worker completes
        self._batch_filenames = batch_keys

        # Show progress UI
        self._progress_label.setText("Re-analyzing…")
        self._progress_bar.setRange(0, len(tracks_to_reanalyze))
        self._progress_bar.setValue(0)
        self._right_stack.setCurrentIndex(0)  # _PAGE_PROGRESS
        self._analyze_action.setEnabled(False)

        # Start async worker
        self._batch_worker = BatchReanalyzeWorker(
            tracks_to_reanalyze,
            self._session.detectors,
            self._session.processors,
            run_detectors=run_detectors,
        )
        self._batch_worker.progress.connect(self._on_worker_progress)
        self._batch_worker.progress_value.connect(self._on_worker_progress_value)
        self._batch_worker.track_done.connect(self._on_batch_track_done)
        self._batch_worker.batch_finished.connect(self._on_batch_done)
        self._batch_worker.error.connect(self._on_batch_error)
        self._batch_worker.start()

    @Slot(str)
    def _on_batch_track_done(self, filename: str):
        """Update one table row after the worker finishes re-analyzing it."""
        self._update_track_row(filename)

    @Slot()
    def _on_batch_done(self):
        """Finalize the batch: restore selection, switch back to tabs."""
        self._batch_worker = None
        self._analyze_action.setEnabled(True)
        self._right_stack.setCurrentIndex(1)  # _PAGE_TABS

        # Re-enable sorting (was disabled in _batch_apply_combo);
        # rows may reorder, so restore selection by key afterward.
        self._track_table.setSortingEnabled(True)
        self._track_table.restore_selection(self._batch_filenames)
        self._batch_filenames = set()

        # Refresh setup table and file tab
        self._populate_setup_table()
        if self._current_track:
            self._refresh_file_tab(self._current_track)

    @Slot(str)
    def _on_batch_error(self, message: str):
        """Handle fatal error from the batch worker."""
        self._batch_worker = None
        self._analyze_action.setEnabled(True)
        self._track_table.setSortingEnabled(True)
        self._track_table.restore_selection(self._batch_filenames)
        self._batch_filenames = set()
        self._right_stack.setCurrentIndex(1)  # _PAGE_TABS
        self._status_bar.showMessage(f"Batch error: {message}")

    # ── Recalculation ────────────────────────────────────────────────────

    def _recalculate_processor(self, track):
        """Re-run the normalization processor for a single track."""
        if not self._session or not self._session.processors:
            return
        for proc in self._session.processors:
            result = proc.process(track)
            result.data["original_gain_db"] = result.gain_db
            track.processor_results[proc.id] = result

    def _reanalyze_single_track(self, track):
        """Re-run all track detectors + processors for a single track (sync)."""
        if not self._session:
            return

        # Re-run track-level detectors (already sorted by dependency)
        for det in self._session.detectors:
            if isinstance(det, TrackDetector):
                try:
                    result = det.analyze(track)
                    track.detector_results[det.id] = result
                except Exception:
                    pass

        # Re-run processors
        self._recalculate_processor(track)

        # Re-apply group levels for any gain-linked groups this track belongs to
        self._apply_linked_group_levels()

        # Update UI
        self._update_track_row(track.filename)
        self._refresh_file_tab(track)

    # ── Track-row UI helpers ─────────────────────────────────────────────

    def _update_track_row(self, filename: str):
        """Refresh analysis label, classification, gain, and sort items
        for the table row matching *filename*.

        Called from:
        - ``_reanalyze_single_track`` (sync single-track path)
        - ``_on_batch_track_done`` (per-track signal from async worker)
        """
        if not self._session:
            return
        track = next(
            (t for t in self._session.tracks if t.filename == filename), None
        )
        if not track:
            return
        row = self._find_table_row(filename)
        if row < 0:
            return

        # Analysis label
        dets = self._session.detectors
        _plain, html, _color, sort_key = track_analysis_label(track, dets)
        lbl, item = _make_analysis_cell(html, sort_key)
        self._track_table.setItem(row, 2, item)
        self._track_table.setCellWidget(row, 2, lbl)

        # Gain spin box + sort item + classification
        pr = next(iter(track.processor_results.values()), None)
        new_gain = pr.gain_db if pr else 0.0
        base_cls = None
        if pr:
            cls_text = pr.classification or "Unknown"
            if "Transient" in cls_text:
                base_cls = "Transient"
            elif cls_text == "Skip":
                base_cls = "Skip"
            else:
                base_cls = "Sustained"

        spin = self._track_table.cellWidget(row, 4)
        if isinstance(spin, QDoubleSpinBox):
            spin.blockSignals(True)
            spin.setValue(new_gain)
            if base_cls is not None:
                spin.setEnabled(base_cls != "Skip")
            spin.blockSignals(False)
        gain_sort = self._track_table.item(row, 4)
        if gain_sort:
            gain_sort.setText(f"{new_gain:+.1f}")
            gain_sort._sort_key = new_gain

        if base_cls is not None:
            cls_combo = self._track_table.cellWidget(row, 3)
            if isinstance(cls_combo, QComboBox):
                cls_combo.blockSignals(True)
                cls_combo.setCurrentText(base_cls)
                cls_combo.blockSignals(False)
                self._style_classification_combo(cls_combo, base_cls)
            sort_item = self._track_table.item(row, 3)
            if sort_item:
                sort_item.setText(base_cls)
                sort_item._sort_key = base_cls.lower()

        # Re-apply row group color (new items lose their background)
        self._apply_row_group_color(row, track.group)

        # Keep the Session Setup table in sync
        self._populate_setup_table()

    def _refresh_file_tab(self, track):
        """Refresh File tab + waveform overlays if *track* is displayed."""
        if not self._current_track or self._current_track.filename != track.filename:
            return
        html = render_track_detail_html(track, self._session,
                                        show_clean=self._show_clean,
                                        verbose=self._verbose)
        self._file_report.setHtml(self._wrap_html(html))
        all_issues = []
        for result in track.detector_results.values():
            all_issues.extend(getattr(result, "issues", []))
        self._update_overlay_menu(all_issues)

    # ── Table fitting ────────────────────────────────────────────────────

    def _auto_fit_track_table(self):
        """Shrink the left panel to fit the track table columns, giving
        more space to the right detail panel.

        Temporarily switches the File column from Stretch to
        ResizeToContents so we can measure its true content width,
        then adjusts the splitter and restores Stretch mode.
        """
        header = self._track_table.horizontalHeader()

        # Temporarily fit File column to content so we get a true width
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._track_table.resizeColumnToContents(0)
        total_w = sum(header.sectionSize(c) for c in range(header.count()))
        # Restore File column to Stretch
        header.setSectionResizeMode(0, QHeaderView.Stretch)

        # vertical-header (hidden=0) + scrollbar (~20) + frame borders (~4)
        vhw = self._track_table.verticalHeader().width() if self._track_table.verticalHeader().isVisible() else 0
        padding = vhw + 20 + 4
        needed = total_w + padding

        splitter_total = self._main_splitter.width()
        if splitter_total > 0:
            right_w = max(splitter_total - needed, 300)
            left_w = splitter_total - right_w
            self._main_splitter.setSizes([left_w, right_w])

    def _auto_fit_group_column(self):
        """Resize the Group column (6) to fit the widest current combo text."""
        max_w = 0
        for row in range(self._track_table.rowCount()):
            w = self._track_table.cellWidget(row, 6)
            if isinstance(w, BatchComboBox):
                fm = w.fontMetrics()
                tw = fm.horizontalAdvance(w.currentText())
                max_w = max(max_w, tw)
        if max_w > 0:
            # icon (16) + icon gap (4) + text + dropdown arrow (~24) + margins (16)
            needed = 16 + 4 + max_w + 24 + 16
            header = self._track_table.horizontalHeader()
            header.resizeSection(6, max(needed, 100))
