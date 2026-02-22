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

    def __init__(self, processor: DawProcessor, session, output_path: str):
        super().__init__()
        self._processor = processor
        self._session = session
        self._output_path = output_path

    def _on_progress(self, current: int, total: int, message: str):
        self.progress.emit(message)
        self.progress_value.emit(current, total)

    def run(self):
        try:
            results = self._processor.transfer(
                self._session, self._output_path, progress_cb=self._on_progress)
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


class TopoAudioResolveWorker(QThread):
    """Load source audio and resolve a TopologyEntry off the UI thread.

    Emits ``finished`` with ``(audio_ndarray, samplerate)`` on success.
    """

    finished = Signal(object, int)  # (audio_data, samplerate)
    error = Signal(str)

    def __init__(self, entry, source_dir: str, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._source_dir = source_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import os
            import soundfile as sf
            from sessionpreplib.topology import resolve_entry_audio

            track_audio: dict[str, tuple] = {}
            sr = 44100

            for src in self._entry.sources:
                if self._cancelled:
                    return
                path = os.path.join(self._source_dir, src.input_filename)
                data, file_sr = sf.read(path, dtype='float64')
                track_audio[src.input_filename] = (data, file_sr)
                sr = file_sr  # use last samplerate (all should match)

            if self._cancelled:
                return

            resolved = resolve_entry_audio(self._entry, track_audio)
            self.finished.emit(resolved, sr)
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


class TopologyApplyWorker(QThread):
    """Resolve channel topology and write rerouted files to output dir.

    This worker loads source audio on demand, applies the channel routing
    defined in the session topology, and writes the resulting files.
    No audio processors are applied — that is Phase 2's job.
    """

    progress = Signal(str)
    progress_value = Signal(int, int)
    finished = Signal()
    error = Signal(str)

    def __init__(self, session, output_dir: str):
        super().__init__()
        self._session = session
        self._output_dir = output_dir

    def run(self):
        try:
            import os
            import numpy as np
            import soundfile as sf
            from sessionpreplib.audio import load_track, AUDIO_EXTENSIONS
            from sessionpreplib.topology import resolve_entry_audio
            from sessionpreplib.models import TrackContext

            session = self._session
            topology = session.topology
            if topology is None:
                self.error.emit("No topology defined.")
                return

            output_dir = self._output_dir

            # Clean stale audio files from output dir
            if os.path.isdir(output_dir):
                for fname in os.listdir(output_dir):
                    if os.path.splitext(fname)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    fp = os.path.join(output_dir, fname)
                    try:
                        if os.path.isfile(fp):
                            os.unlink(fp)
                    except OSError:
                        pass
            os.makedirs(output_dir, exist_ok=True)

            # Collect source filenames referenced by topology
            ok_tracks = [t for t in session.tracks if t.status == "OK"]
            track_map = {t.filename: t for t in ok_tracks}

            needed_sources: set[str] = set()
            for entry in topology.entries:
                for src in entry.sources:
                    needed_sources.add(src.input_filename)

            total = len(needed_sources) + len(topology.entries)

            # Phase A: Load source audio
            source_audio: dict[str, tuple] = {}
            for step, filename in enumerate(sorted(needed_sources)):
                self.progress.emit(f"Loading {filename}")
                self.progress_value.emit(step, total)
                track = track_map.get(filename)
                if not track:
                    continue
                if track.audio_data is None or track.audio_data.size == 0:
                    loaded = load_track(track.filepath)
                    track.audio_data = loaded.audio_data
                    track.samplerate = loaded.samplerate
                    track.total_samples = loaded.total_samples
                source_audio[filename] = (track.audio_data, track.samplerate)

            # Phase B: Resolve topology + write output files
            output_tracks = []
            errors = []
            base_step = len(needed_sources)
            for idx, entry in enumerate(topology.entries):
                step = base_step + idx
                self.progress.emit(f"Writing {entry.output_filename}")
                self.progress_value.emit(step, total)

                try:
                    resolved = resolve_entry_audio(entry, source_audio)
                    src_track = track_map.get(
                        entry.sources[0].input_filename) if entry.sources else None
                    sr = src_track.samplerate if src_track else 44100
                    subtype = src_track.subtype if src_track else "PCM_24"
                    bitdepth = src_track.bitdepth if src_track else "24-bit"

                    dst = os.path.join(output_dir, entry.output_filename)
                    sf.write(dst, resolved, sr, subtype=subtype)

                    n_samples = resolved.shape[0]
                    out_tc = TrackContext(
                        filename=entry.output_filename,
                        filepath=dst,
                        audio_data=None,
                        samplerate=sr,
                        channels=entry.output_channels,
                        total_samples=n_samples,
                        bitdepth=bitdepth,
                        subtype=subtype,
                        duration_sec=(n_samples / sr) if sr > 0 else 0.0,
                        group=src_track.group if src_track else None,
                    )
                    output_tracks.append(out_tc)
                except Exception as e:
                    errors.append((entry.output_filename, str(e)))

            self.progress_value.emit(total, total)

            session.output_tracks = output_tracks
            session.config["_topology_apply_errors"] = errors
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
