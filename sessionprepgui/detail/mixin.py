"""Detail view mixin: file detail, waveform, overlays, and playback."""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QAction

from sessionpreplib.audio import get_window_samples

from ..helpers import fmt_time
from .report import render_summary_html, render_track_detail_html
from ..tracks.table_widgets import _TAB_FILE, _TAB_SUMMARY, _PHASE_TOPOLOGY
from ..theme import COLORS
from ..analysis.worker import AudioLoadWorker
from ..waveform.compute import WaveformLoadWorker


class DetailMixin:
    """File detail view, waveform display, overlays, and playback.

    Mixed into ``SessionPrepWindow`` — not meant to be used standalone.
    """

    # ── Report rendering ──────────────────────────────────────────────────

    @property
    def _show_clean(self) -> bool:
        if self._session_config is not None:
            cfg = self._read_session_config()
            return cfg.get("presentation", {}).get(
                "show_clean_detectors", False)
        preset = self._active_preset()
        return preset.get("presentation", {}).get("show_clean_detectors", False)

    @property
    def _verbose(self) -> bool:
        return self._config.get("app", {}).get("report_verbosity", "normal") == "verbose"

    def _render_summary(self):
        """Render the diagnostic summary into the Summary tab."""
        if not self._summary or not self._session:
            return
        html = render_summary_html(
            self._summary, show_faders=False,
            show_clean=self._show_clean,
        )
        self._summary_view.setHtml(self._wrap_html(html))

    def _show_track_detail(self, track):
        """Populate the File tab with per-track detail + waveform.

        The HTML report is rendered and displayed immediately so the UI
        feels responsive.  Waveform loading (dtype conversion, peak
        finding, RMS setup) is deferred to the next event-loop iteration
        via ``QTimer.singleShot`` so the tab switch paints first.
        """
        self._on_stop()
        self._current_track = track

        # Show HTML report immediately
        html = render_track_detail_html(track, self._session,
                                        show_clean=self._show_clean,
                                        verbose=self._verbose)
        self._file_report.setHtml(self._wrap_html(html))

        # Enable and switch to File tab before heavy work
        self._detail_tabs.setTabEnabled(_TAB_FILE, True)
        self._detail_tabs.setCurrentIndex(_TAB_FILE)

        # Defer waveform loading so the UI repaints first
        QTimer.singleShot(0, lambda: self._load_waveform(track))

    def _load_waveform(self, track):
        """Start background waveform loading for *track*."""
        # Guard: user may have clicked a different track while we were queued
        if self._current_track is not track:
            return

        # Cancel any in-flight workers
        if self._wf_worker is not None:
            self._wf_worker.cancel()
            self._wf_worker.finished.disconnect()
            self._wf_worker = None
        if self._audio_load_worker is not None:
            self._audio_load_worker.cancel()
            self._audio_load_worker.finished.disconnect()
            self._audio_load_worker = None

        # If audio_data is absent but the file exists, load it from disk first
        if (track.audio_data is None or track.audio_data.size == 0) and \
                track.status == "OK" and os.path.isfile(track.filepath):
            self._waveform.set_loading(True)
            if self._detail_tabs.currentIndex() == _TAB_FILE:
                self._wf_container.setVisible(True)
            self._play_btn.setEnabled(False)
            self._update_time_label(0)

            worker = AudioLoadWorker(track, parent=self)
            self._audio_load_worker = worker
            worker.finished.connect(
                lambda t, orig=track: self._on_audio_loaded(t, orig))
            worker.error.connect(
                lambda msg: self._on_audio_load_error(msg, track))
            worker.start()
            return

        has_audio = track.audio_data is not None and track.audio_data.size > 0
        if has_audio:
            self._waveform.set_loading(True)
            if self._detail_tabs.currentIndex() == _TAB_FILE:
                self._wf_container.setVisible(True)
            self._play_btn.setEnabled(False)
            self._update_time_label(0)

            flat_cfg = self._flat_config()
            win_ms = flat_cfg.get("window", 400)
            ws = get_window_samples(track, win_ms)

            self._wf_worker = WaveformLoadWorker(
                track.audio_data, track.samplerate, ws,
                spec_n_fft=self._waveform.spec_n_fft,
                spec_window=self._waveform.spec_window,
                parent=self)
            self._wf_worker.finished.connect(
                lambda result, t=track: self._on_waveform_loaded(result, t))
            self._wf_worker.start()
        else:
            self._waveform.set_audio(None, 44100)
            self._update_overlay_menu([])
            if self._detail_tabs.currentIndex() == _TAB_FILE:
                self._wf_container.setVisible(False)
            self._play_btn.setEnabled(False)
            self._update_time_label(0)

    @Slot(object, object)
    def _on_waveform_loaded(self, result: dict, track):
        """Receive pre-computed waveform data from the background worker."""
        self._wf_worker = None

        # Discard if user switched to a different track
        if self._current_track is not track:
            return

        self._waveform.set_precomputed(result)
        cmap = self._config.get("app", {}).get("spectrogram_colormap", "magma")
        self._waveform.set_colormap(cmap)
        # Sync colormap dropdown with preference
        for act in self._cmap_group.actions():
            if act.data() == cmap:
                act.setChecked(True)
                break

        all_issues = []
        for det_result in track.detector_results.values():
            all_issues.extend(getattr(det_result, "issues", []))
        self._waveform.set_issues(all_issues)
        self._update_overlay_menu(all_issues)
        self._wf_panel.update_play_mode_channels(len(result["channels"]))
        self._play_btn.setEnabled(True)
        self._update_time_label(0)

    def _on_audio_loaded(self, track, orig_track):
        """Audio data loaded from disk; proceed to waveform rendering."""
        self._audio_load_worker = None
        # Discard if user switched tracks while we were loading
        if self._current_track is not orig_track:
            return
        # Now kick off the normal waveform worker path
        self._load_waveform(track)

    def _on_audio_load_error(self, message: str, track):
        """Audio file could not be read from disk."""
        self._audio_load_worker = None
        if self._current_track is not track:
            return
        self._waveform.set_audio(None, 44100)
        self._wf_container.setVisible(False)
        self._play_btn.setEnabled(False)
        self._status_bar.showMessage(f"Could not load audio: {message}")

    # ── Overlay dropdown ────────────────────────────────────────────────

    def _update_overlay_menu(self, issues: list):
        """Rebuild the overlay dropdown menu based on current track issues."""
        self._overlay_menu.clear()
        self._waveform.set_enabled_overlays(set())

        if not issues:
            self._overlay_btn.setText("Detector Overlays")
            return

        # Build detector instance map from session
        det_map: dict[str, object] = {}
        det_names: dict[str, str] = {}
        if self._session and hasattr(self._session, "detectors"):
            for d in self._session.detectors:
                det_map[d.id] = d
                det_names[d.id] = d.name

        # Filter out issues from detectors that suppress themselves or are skipped
        track = self._current_track
        filtered_issues = []
        for issue in issues:
            det = det_map.get(issue.label)
            if det and track:
                result = track.detector_results.get(issue.label)
                if result:
                    if hasattr(det, 'effective_severity') and det.effective_severity(result) is None:
                        continue
                    if not det.is_relevant(result, track):
                        continue
            filtered_issues.append(issue)

        if not filtered_issues:
            self._overlay_btn.setText("Detector Overlays")
            return

        # Build {label: count} from filtered issue list
        label_counts: dict[str, int] = {}
        for issue in filtered_issues:
            label_counts[issue.label] = label_counts.get(issue.label, 0) + 1

        # Add a checkable action per detector that has issues
        for label in sorted(label_counts, key=lambda lb: det_names.get(lb, lb).lower()):
            name = det_names.get(label, label)
            count = label_counts[label]
            action = self._overlay_menu.addAction(f"{name} ({count})")
            action.setCheckable(True)
            action.setChecked(False)
            action.setData(label)
            action.toggled.connect(self._on_overlay_toggled)

        self._overlay_btn.setText("Detector Overlays")

    @Slot()
    def _on_overlay_toggled(self):
        """Collect checked overlay labels and update the waveform."""
        checked = set()
        for action in self._overlay_menu.actions():
            if action.isChecked():
                checked.add(action.data())
        self._waveform.set_enabled_overlays(checked)
        n = len(checked)
        self._overlay_btn.setText(f"Detector Overlays ({n})" if n else "Detector Overlays")

    @Slot(QAction)
    def _on_spec_fft_changed(self, action):
        self._waveform.set_spec_fft(int(action.data()))

    @Slot(QAction)
    def _on_spec_window_changed(self, action):
        self._waveform.set_spec_window(action.data())

    @Slot(QAction)
    def _on_spec_cmap_changed(self, action):
        self._waveform.set_colormap(action.data())

    @Slot(QAction)
    def _on_spec_floor_changed(self, action):
        self._waveform.set_spec_db_floor(float(action.data()))

    @Slot(QAction)
    def _on_spec_ceil_changed(self, action):
        self._waveform.set_spec_db_ceil(float(action.data()))

    # ── Playback ──────────────────────────────────────────────────────────

    @Slot()
    def _on_toggle_play(self):
        # Stop always works regardless of which phase started playback
        if self._playback.is_playing:
            self._playback.stop()
            # Reset both phases' transport UI
            self._stop_btn.setEnabled(False)
            if self._current_track is not None:
                self._play_btn.setEnabled(True)
            self._topo_wf_panel.stop_btn.setEnabled(False)
            self._topo_wf_panel.play_btn.setEnabled(
                self._topo_cached_audio is not None)
            return
        # Start based on current tab
        if self._phase_tabs.currentIndex() == _PHASE_TOPOLOGY:
            if self._topo_cached_audio is not None:
                self._on_topo_play()
        elif self._current_track is not None:
            self._on_play()

    @Slot()
    def _on_play(self):
        track = self._current_track
        if track is None or track.audio_data is None:
            return
        self._on_stop()
        start = self._waveform._cursor_sample
        mode, channel = self._wf_panel.play_mode
        self._playback.play(track.audio_data, track.samplerate, start,
                            mode=mode, channel=channel)
        if self._playback.is_playing:
            self._play_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)

    @Slot()
    def _on_stop(self):
        was_playing = self._playback.is_playing
        start_sample = self._playback.play_start_sample
        self._playback.stop()
        self._stop_btn.setEnabled(False)
        if self._current_track is not None:
            self._play_btn.setEnabled(True)
        if was_playing:
            self._waveform.set_cursor(start_sample)
            self._update_time_label(start_sample)

    @Slot(int)
    def _on_cursor_updated(self, sample_pos: int):
        if self._phase_tabs.currentIndex() == _PHASE_TOPOLOGY:
            self._topo_wf_panel.waveform.set_cursor(sample_pos)
            self._topo_update_time_label(sample_pos)
            return
        self._waveform.set_cursor(sample_pos)
        self._update_time_label(sample_pos)

    @Slot()
    def _on_playback_finished(self):
        if self._phase_tabs.currentIndex() == _PHASE_TOPOLOGY:
            self._on_topo_stop()
            return
        self._stop_btn.setEnabled(False)
        if self._current_track is not None:
            self._play_btn.setEnabled(True)
        self._waveform.set_cursor(0)
        self._update_time_label(0)

    @Slot(str)
    def _on_playback_error(self, message: str):
        self._status_bar.showMessage(f"Playback error: {message}")

    @Slot(int)
    def _on_waveform_seek(self, sample_index: int):
        if self._playback.is_playing:
            self._on_stop()
            self._waveform.set_cursor(sample_index)
            self._on_play()
        else:
            self._update_time_label(sample_index)

    def _update_time_label(self, sample_pos: int = 0):
        track = self._current_track
        if track is None or track.samplerate <= 0:
            self._time_label.setText("00:00 / 00:00")
            return
        sr = track.samplerate
        self._time_label.setText(
            f"{fmt_time(sample_pos / sr)} / {fmt_time(track.total_samples / sr)}"
            f"  \u2022  {sample_pos:,}"
        )
