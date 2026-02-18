"""Background worker threads for pipeline analysis."""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from sessionpreplib.daw_processor import DawProcessor
from sessionpreplib.detector import TrackDetector
from sessionpreplib.pipeline import Pipeline, load_session
from sessionpreplib.detectors import default_detectors
from sessionpreplib.processors import default_processors
from sessionpreplib.rendering import build_diagnostic_summary
from sessionpreplib.events import EventBus


class DawCheckWorker(QThread):
    """Runs DawProcessor.check_connectivity() off the main thread."""

    result = Signal(bool, str)  # (ok, message)

    def __init__(self, processor: DawProcessor):
        super().__init__()
        self._processor = processor

    def run(self):
        try:
            ok, msg = self._processor.check_connectivity()
            self.result.emit(ok, msg)
        except Exception as e:
            self.result.emit(False, str(e))


class DawFetchWorker(QThread):
    """Runs DawProcessor.fetch() off the main thread."""

    result = Signal(bool, str, object)  # (ok, message, session_or_none)

    def __init__(self, processor: DawProcessor, session):
        super().__init__()
        self._processor = processor
        self._session = session

    def run(self):
        try:
            session = self._processor.fetch(self._session)
            self.result.emit(True, "Fetch complete", session)
        except Exception as e:
            self.result.emit(False, str(e), None)


class DawTransferWorker(QThread):
    """Runs DawProcessor.transfer() off the main thread with progress."""

    progress = Signal(str)              # status text
    progress_value = Signal(int, int)   # (current, total)
    result = Signal(bool, str, object)  # (ok, message, results_list)

    def __init__(self, processor: DawProcessor, session):
        super().__init__()
        self._processor = processor
        self._session = session

    def _on_progress(self, current: int, total: int, message: str):
        self.progress.emit(message)
        self.progress_value.emit(current, total)

    def run(self):
        try:
            results = self._processor.transfer(
                self._session, progress_cb=self._on_progress)
            failures = [r for r in results if not r.success]
            if failures:
                msg = f"Transfer done: {len(results) - len(failures)}/{len(results)} OK"
            else:
                msg = f"Transfer complete ({len(results)} operations)"
            self.result.emit(True, msg, results)
        except Exception as e:
            self.result.emit(False, str(e), None)


class AnalyzeWorker(QThread):
    """Runs pipeline analysis in a background thread."""

    progress = Signal(str)                # descriptive text
    progress_value = Signal(int, int)     # (current_step, total_steps)
    track_analyzed = Signal(str, object)  # (filename, track) after detectors
    track_planned = Signal(str, object)   # (filename, track) after processors
    finished = Signal(object, object)     # (session, diagnostic_summary)
    error = Signal(str)

    def __init__(self, source_dir: str, config: dict):
        super().__init__()
        self.source_dir = source_dir
        self.config = config

    def run(self):
        try:
            event_bus = EventBus()

            self.progress.emit("Loading session\u2026")
            session = load_session(self.source_dir, self.config, event_bus=event_bus)

            if not session.tracks:
                self.error.emit("No audio files found in directory.")
                return

            self.progress.emit("Building pipeline\u2026")
            detectors = default_detectors()
            processors = default_processors()
            pipeline = Pipeline(
                detectors=detectors,
                audio_processors=processors,
                config=self.config,
                event_bus=event_bus,
            )

            # Calculate total progress steps
            ok_tracks = [t for t in session.tracks if t.status == "OK"]
            num_ok = len(ok_tracks)
            num_track_det = len(pipeline.track_detectors)
            num_session_det = len(pipeline.session_detectors)
            num_proc = len(pipeline.audio_processors)
            total_steps = (
                num_ok * num_track_det      # analyze: per-track detectors
                + num_session_det           # analyze: session detectors
                + num_ok * num_proc         # plan: per-track processors
                + 1                         # build summary
            )
            self._step = 0
            self._total = max(total_steps, 1)
            self._step_lock = threading.Lock()

            # Track map for emitting track objects
            track_map = {t.filename: t for t in session.tracks}

            # Subscribe to pipeline events (called from pool threads)
            def on_detector_complete(detector_id, filename, **_kw):
                with self._step_lock:
                    self._step += 1
                    step = self._step
                self.progress.emit(f"Analyzing {filename} \u2014 {detector_id}")
                self.progress_value.emit(step, self._total)

            def on_session_detector_complete(detector_id, **_kw):
                with self._step_lock:
                    self._step += 1
                    step = self._step
                self.progress.emit(f"Session detector \u2014 {detector_id}")
                self.progress_value.emit(step, self._total)

            def on_track_analyze_complete(filename, **_kw):
                track = track_map.get(filename)
                if track:
                    self.track_analyzed.emit(filename, track)

            def on_processor_complete(processor_id, filename, **_kw):
                with self._step_lock:
                    self._step += 1
                    step = self._step
                self.progress.emit(f"Planning {filename} \u2014 {processor_id}")
                self.progress_value.emit(step, self._total)

            def on_track_plan_complete(filename, **_kw):
                track = track_map.get(filename)
                if track:
                    self.track_planned.emit(filename, track)

            event_bus.subscribe("detector.complete", on_detector_complete)
            event_bus.subscribe("session_detector.complete", on_session_detector_complete)
            event_bus.subscribe("track.analyze_complete", on_track_analyze_complete)
            event_bus.subscribe("processor.complete", on_processor_complete)
            event_bus.subscribe("track.plan_complete", on_track_plan_complete)

            # Phase 1: Analyze (per-track detectors run in parallel)
            self.progress.emit("Analyzing\u2026")
            self.progress_value.emit(0, self._total)
            session = pipeline.analyze(session)

            # Phase 2: Plan (per-track processors run in parallel)
            self.progress.emit("Planning\u2026")
            session = pipeline.plan(session)

            # Build summary
            with self._step_lock:
                self._step += 1
                step = self._step
            self.progress.emit("Building summary\u2026")
            self.progress_value.emit(step, self._total)
            summary = build_diagnostic_summary(session)

            self.finished.emit(session, summary)
        except Exception as e:
            self.error.emit(str(e))


class AudioLoadWorker(QThread):
    """Load audio data from disk for a single track (no analysis).

    Used when a session is loaded from file and ``track.audio_data`` is
    ``None`` but the source file still exists on disk.  Emits ``finished``
    with the populated track on success, or ``error`` with a message.
    """

    finished = Signal(object)   # track with audio_data populated
    error = Signal(str)

    def __init__(self, track, parent=None):
        super().__init__(parent)
        self._track = track
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from sessionpreplib.audio import load_track
            import soundfile as sf
            import numpy as np
            data, sr = sf.read(self._track.filepath, dtype='float64')
            if self._cancelled:
                return
            self._track.audio_data = data
            self._track.samplerate = sr
            self._track.total_samples = len(data)
            self.finished.emit(self._track)
        except Exception as exc:
            self.error.emit(str(exc))


class PrepareWorker(QThread):
    """Runs Pipeline.prepare() off the main thread with progress."""

    progress = Signal(str)              # status text
    progress_value = Signal(int, int)   # (current, total)
    track_prepared = Signal(str)        # filename
    finished = Signal()
    error = Signal(str)

    def __init__(self, session, processors, output_dir: str):
        super().__init__()
        self._session = session
        self._processors = processors
        self._output_dir = output_dir

    def _on_progress(self, current: int, total: int, message: str):
        self.progress.emit(message)
        self.progress_value.emit(current, total)

    def run(self):
        try:
            from sessionpreplib.pipeline import Pipeline

            # Build a lightweight pipeline with just the processors
            pipeline = Pipeline(
                detectors=[],
                audio_processors=self._processors,
                config=self._session.config,
            )
            pipeline.prepare(
                self._session,
                self._output_dir,
                progress_cb=self._on_progress,
            )
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class BatchReanalyzeWorker(QThread):
    """Re-run detectors and/or processors for a subset of tracks.

    Used by the batch-edit workflow: after the user applies a change
    (e.g. RMS anchor override) to multiple tracks at once, this worker
    performs the heavy re-analysis in the background so the GUI stays
    responsive.

    Parameters
    ----------
    tracks:
        The tracks to re-analyze.
    detectors:
        Configured detector instances (from ``session.detectors``).
    processors:
        Configured processor instances (from ``session.processors``).
    run_detectors:
        If ``True`` (default), re-run track-level detectors before
        processors.  Set to ``False`` for lightweight changes that
        only need processor re-calculation (e.g. classification override).
    """

    progress = Signal(str)
    progress_value = Signal(int, int)     # (current, total)
    track_done = Signal(str)              # filename
    batch_finished = Signal()             # renamed to avoid QThread.finished collision
    error = Signal(str)

    def __init__(self, tracks, detectors, processors,
                 run_detectors: bool = True):
        super().__init__()
        self._tracks = list(tracks)
        self._detectors = detectors
        self._processors = processors
        self._run_detectors = run_detectors

    def run(self):
        try:
            total = len(self._tracks)
            for i, track in enumerate(self._tracks):
                self.progress.emit(f"Re-analyzing {track.filename}\u2026")
                self.progress_value.emit(i, total)

                if self._run_detectors:
                    for det in self._detectors:
                        if isinstance(det, TrackDetector):
                            try:
                                result = det.analyze(track)
                                track.detector_results[det.id] = result
                            except Exception:
                                pass

                for proc in self._processors:
                    try:
                        result = proc.process(track)
                        track.processor_results[proc.id] = result
                    except Exception:
                        pass

                self.track_done.emit(track.filename)

            self.progress_value.emit(total, total)
            self.batch_finished.emit()
        except Exception as e:
            self.error.emit(str(e))
