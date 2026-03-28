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
from sessionpreplib.models import LifecyclePhase


class DawCheckWorker(QThread):
    """Runs DawProcessor.check_connectivity() off the main thread."""

    result = Signal(bool, str)  # (ok, message)

    def __init__(self, processor: DawProcessor, parent=None):
        super().__init__(parent)
        self._processor = processor

    def run(self):
        try:
            ok, msg = self._processor.check_connectivity()
            self.result.emit(ok, msg)
        except Exception as e:
            self.result.emit(False, str(e))


class DawFetchWorker(QThread):
    """Runs DawProcessor.fetch() off the main thread."""

    progress = Signal(str)              # status text
    progress_value = Signal(int, int)   # (current, total)
    result = Signal(bool, str, object)  # (ok, message, session_or_none)

    def __init__(self, processor: DawProcessor, session, parent=None):
        super().__init__(parent)
        self._processor = processor
        self._session = session

    def _on_progress(self, current: int, total: int, message: str):
        self.progress.emit(message)
        self.progress_value.emit(current, total)

    def run(self):
        try:
            # Provide the progress callback if the processor supports it
            try:
                session = self._processor.fetch(self._session, progress_cb=self._on_progress)
            except TypeError:
                # Fallback for processors that don't support progress_cb yet
                session = self._processor.fetch(self._session)
            self.result.emit(True, "Fetch complete", session)
        except Exception as e:
            self.result.emit(False, str(e), None)


class DawTransferWorker(QThread):
    """Runs DawProcessor.transfer() off the main thread with progress."""

    progress = Signal(str)              # status text
    progress_value = Signal(int, int)   # (current, total)
    result = Signal(bool, str, object)  # (ok, message, results_list)

    def __init__(self, processor: DawProcessor, session, output_path: str, parent=None, close_session: bool = True):
        super().__init__(parent)
        self._processor = processor
        self._session = session
        self._output_path = output_path
        self._close_session = close_session

    def _on_progress(self, current: int, total: int, message: str):
        self.progress.emit(message)
        self.progress_value.emit(current, total)

    def run(self):
        try:
            results = self._processor.transfer(
                self._session, self._output_path, progress_cb=self._on_progress, close_when_done=self._close_session)
            failures = [r for r in results if not r.success]
            if failures:
                msg = f"Transfer done: {len(results) - len(failures)}/{len(results)} OK"
            else:
                msg = f"Transfer complete ({len(results)} operations)"
            self.result.emit(True, msg, results)
        except Exception as e:
            self.result.emit(False, str(e), None)


class Phase1AnalyzeWorker(QThread):
    """Runs Phase 1 (Structural & Format) pipeline analysis in a background thread."""

    progress = Signal(str)                # descriptive text
    progress_value = Signal(int, int)     # (current_step, total_steps)
    track_analyzed = Signal(str, object)  # (filename, track) after detectors
    finished = Signal(object)             # (session)
    error = Signal(str)

    def __init__(self, session_context: object, config: dict):
        super().__init__()
        self.session_context = session_context
        self.config = config

    def run(self):
        try:
            event_bus = EventBus()

            # Use the already loaded session
            session = self.session_context

            if not session.tracks:
                self.error.emit("No audio files found in session.")
                return

            self.progress.emit("Building pipeline\u2026")
            detectors = default_detectors()
            pipeline = Pipeline(
                detectors=detectors,
                config=self.config,
                event_bus=event_bus,
            )

            # Calculate total progress steps for Phase 1
            ok_tracks = [t for t in session.tracks if t.status == "OK"]
            num_ok = len(ok_tracks)

            p1_track_dets = [d for d in pipeline.track_detectors if getattr(d, 'phase', LifecyclePhase.PHASE2) == LifecyclePhase.PHASE1]
            p1_sess_dets = [d for d in pipeline.session_detectors if getattr(d, 'phase', LifecyclePhase.PHASE2) == LifecyclePhase.PHASE1]

            num_track_det = len(p1_track_dets)
            num_session_det = len(p1_sess_dets)

            # Load audio data first, since lightweight discovery doesn't load it
            if num_ok > 0 and ok_tracks[0].audio_data is None:
                from sessionpreplib.audio import load_track
                from concurrent.futures import ThreadPoolExecutor, as_completed
                import os

                self.progress.emit("Loading audio data\u2026")
                self.progress_value.emit(0, num_ok)

                with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as pool:
                    futures = {
                        pool.submit(load_track, t.filepath): t
                        for t in ok_tracks
                    }
                    loaded = 0
                    for future in as_completed(futures):
                        t = futures[future]
                        try:
                            res = future.result()
                            t.audio_data = res.audio_data
                            # Preserve metadata since it might be updated
                            t.total_samples = res.total_samples
                            t.samplerate = res.samplerate
                            # VERY IMPORTANT: clear cache in case `get_peak()` was called
                            # while audio_data was None, so it doesn't stay 0.0 forever.
                            t._cache.clear()
                        except Exception as e:
                            t.status = "ERROR"
                            t.error = str(e)
                        loaded += 1
                        self.progress_value.emit(loaded, num_ok)

            # Re-filter OK tracks after loading (some might have failed)
            ok_tracks = [t for t in session.tracks if t.status == "OK"]
            num_ok = len(ok_tracks)

            total_steps = (
                num_ok * num_track_det
                + num_session_det
            )
            self._step = 0
            self._total = max(total_steps, 1)
            self._step_lock = threading.Lock()

            # Track map for emitting track objects
            track_map = {t.filename: t for t in session.tracks}

            # Subscribe to pipeline events
            def on_detector_complete(detector_id, filename, **_kw):
                with self._step_lock:
                    self._step += 1
                    step = self._step
                self.progress.emit(f"Checking {filename} \u2014 {detector_id}")
                self.progress_value.emit(step, self._total)

            def on_session_detector_complete(detector_id, **_kw):
                with self._step_lock:
                    self._step += 1
                    step = self._step
                self.progress.emit(f"Session check \u2014 {detector_id}")
                self.progress_value.emit(step, self._total)

            def on_track_analyze_complete(filename, **_kw):
                track = track_map.get(filename)
                if track:
                    self.track_analyzed.emit(filename, track)

            event_bus.subscribe("detector.complete", on_detector_complete)
            event_bus.subscribe("session_detector.complete", on_session_detector_complete)
            event_bus.subscribe("track.analyze_complete", on_track_analyze_complete)

            # Phase 1: Analyze
            self.progress.emit("Validating Layout\u2026")
            self.progress_value.emit(0, self._total)
            session = pipeline.analyze_phase1(session)

            self.finished.emit(session)
        except Exception as e:
            self.error.emit(str(e))


class AnalyzeWorker(QThread):
    """Runs pipeline analysis in a background thread."""

    progress = Signal(str)                # descriptive text
    progress_value = Signal(int, int)     # (current_step, total_steps)
    track_analyzed = Signal(str, object)  # (filename, track) after detectors
    track_planned = Signal(str, object)   # (filename, track) after processors
    finished = Signal(object, object)     # (session, diagnostic_summary)
    error = Signal(str)

    def __init__(self, source_dir: str, config: dict, recursive: bool = False):
        super().__init__()
        self.source_dir = source_dir
        self.config = config
        self.recursive = recursive

    def run(self):
        try:
            event_bus = EventBus()

            self.progress.emit("Loading session\u2026")
            session = load_session(self.source_dir, self.config, event_bus=event_bus,
                                  recursive=self.recursive)

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
            num_track_det = len([d for d in pipeline.track_detectors if getattr(d, 'phase', LifecyclePhase.PHASE2) == LifecyclePhase.PHASE2])
            num_session_det = len([d for d in pipeline.session_detectors if getattr(d, 'phase', LifecyclePhase.PHASE2) == LifecyclePhase.PHASE2])
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

            # Phase 2: Analyze (per-track detectors run in parallel)
            self.progress.emit("Analyzing Content\u2026")
            self.progress_value.emit(0, self._total)
            session = pipeline.analyze_phase2(session)

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
            import time, logging
            t0 = time.perf_counter()
            log = logging.getLogger(__name__)

            from sessionpreplib.audio import load_track
            import soundfile as sf
            import numpy as np
            data, sr = sf.read(self._track.filepath, dtype='float64')
            
            elapsed = (time.perf_counter() - t0) * 1000
            if getattr(self._track, 'filename', None):
                log.debug("[Trace] AudioLoadWorker I/O (sf.read) for '%s': %.2f ms", self._track.filename, elapsed)

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


class TopoMultiAudioWorker(QThread):
    """Load multiple tracks and produce stacked display + downmixed playback.

    Emits ``finished(display_audio, playback_audio, samplerate, labels)``
    where *display_audio* has all tracks' channels concatenated and
    *playback_audio* is summed by channel position.
    """

    finished = Signal(object, object, int, list)  # display, playback, sr, labels
    error = Signal(str)

    def __init__(self, items: list, side: str, source_dir: str, parent=None):
        """
        Parameters
        ----------
        items : list
            For ``side="input"``: list of ``(filepath, display_name, n_channels)``.
            For ``side="output"``: list of ``(TopologyEntry, display_name)``.
        side : str
            ``"input"`` or ``"output"``.
        source_dir : str
            Root directory for source audio files.
        """
        super().__init__(parent)
        self._items = items
        self._side = side
        self._source_dir = source_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            import os
            import numpy as np
            import soundfile as sf

            track_arrays = []   # list of 2-D arrays (samples, ch)
            track_ch_counts = []
            track_labels_list = []
            sr = 44100

            from sessionprepgui.waveform.panel import WaveformPanel

            if self._side == "input":
                for item in self._items:
                    if self._cancelled:
                        return
                    filepath = item[0]
                    name = item[1]
                    channels_to_keep = item[2] if len(item) > 2 else None

                    data, file_sr = sf.read(filepath, dtype='float64')
                    sr = file_sr
                    if data.ndim == 1:
                        data = data.reshape(-1, 1)

                    if channels_to_keep is not None:
                        data = data[:, channels_to_keep]
                        ch_labels = [f"{name} Ch{c}" for c in channels_to_keep]
                    else:
                        n_ch = data.shape[1]
                        ch_labels = []
                        names = WaveformPanel._CHANNEL_LABELS.get(n_ch)
                        for c in range(n_ch):
                            if names and c < len(names):
                                ch_labels.append(f"{name} {names[c]}")
                            else:
                                ch_labels.append(f"{name} Ch{c}")

                    track_arrays.append(data)
                    track_ch_counts.append(data.shape[1])
                    track_labels_list.append(ch_labels)
            else:  # output
                from sessionpreplib.topology import resolve_entry_audio
                for item in self._items:
                    if self._cancelled:
                        return
                    entry = item[0]
                    name = item[1]
                    channels_to_keep = item[2] if len(item) > 2 else None

                    # Load source audio for this entry
                    track_audio: dict[str, tuple] = {}
                    for src in entry.sources:
                        if self._cancelled:
                            return
                        path = os.path.join(self._source_dir, src.input_filename)
                        data, file_sr = sf.read(path, dtype='float64')
                        track_audio[src.input_filename] = (data, file_sr)
                        sr = file_sr
                    
                    resolved = resolve_entry_audio(entry, track_audio)
                    if resolved.ndim == 1:
                        resolved = resolved.reshape(-1, 1)

                    if channels_to_keep is not None:
                        resolved = resolved[:, channels_to_keep]
                        ch_labels = [f"{name} Ch{c}" for c in channels_to_keep]
                    else:
                        n_ch = resolved.shape[1]
                        ch_labels = []
                        names = WaveformPanel._CHANNEL_LABELS.get(n_ch)
                        for c in range(n_ch):
                            if names and c < len(names):
                                ch_labels.append(f"{name} {names[c]}")
                            else:
                                ch_labels.append(f"{name} Ch{c}")

                    track_arrays.append(resolved)
                    track_ch_counts.append(resolved.shape[1])
                    track_labels_list.append(ch_labels)

            if self._cancelled or not track_arrays:
                return

            # --- Build display audio (all channels concatenated) ---
            max_samples = max(a.shape[0] for a in track_arrays)
            padded = []
            for a in track_arrays:
                if a.shape[0] < max_samples:
                    pad = np.zeros((max_samples - a.shape[0], a.shape[1]),
                                   dtype=np.float64)
                    a = np.vstack([a, pad])
                padded.append(a)
            display_audio = np.hstack(padded)  # (max_samples, total_ch)

            # --- Build playback audio (summed by channel position) ---
            max_ch = max(track_ch_counts)
            n_tracks = len(track_arrays)
            playback = np.zeros((max_samples, max_ch), dtype=np.float64)
            for a in padded:
                playback[:, :a.shape[1]] += a
            playback /= n_tracks

            # Squeeze mono
            if playback.shape[1] == 1:
                playback = playback[:, 0]
            if display_audio.shape[1] == 1:
                display_audio = display_audio[:, 0]

            # --- Channel labels ---
            labels = []
            for lst in track_labels_list:
                labels.extend(lst)

            self.finished.emit(display_audio, playback, sr, labels)
        except Exception as exc:
            self.error.emit(str(exc))


class PrepareWorker(QThread):
    """Runs Pipeline.prepare() off the main thread with progress."""

    progress = Signal(str)              # status text
    progress_value = Signal(int, int)   # (current, total)
    track_prepared = Signal(str)        # filename
    prepare_finished = Signal()         # renamed: avoid shadowing QThread.finished
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
            self.prepare_finished.emit()
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
    apply_finished = Signal()           # renamed: avoid shadowing QThread.finished
    error = Signal(str)

    def __init__(self, session, output_dir: str, source_dir: str | None = None,
                 peaks_dir: str | None = None):
        super().__init__()
        self._session = session
        self._output_dir = output_dir
        self._source_dir = source_dir
        self._peaks_dir = peaks_dir

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

            # Clean stale audio files from output dir (recursive)
            if os.path.isdir(output_dir):
                for dirpath, _dirnames, filenames in os.walk(
                        output_dir, topdown=False):
                    for fname in filenames:
                        if os.path.splitext(fname)[1].lower() not in AUDIO_EXTENSIONS:
                            continue
                        fp = os.path.join(dirpath, fname)
                        try:
                            os.unlink(fp)
                        except OSError:
                            pass
                    # Prune empty subdirectories
                    if dirpath != output_dir:
                        try:
                            os.rmdir(dirpath)
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
            # After a previous Apply+Analyze cycle, session.tracks may
            # only contain *output* filenames.  Sources that were merged
            # (e.g. 12_ElecGtr2.wav into 11_ElecGtr1.wav) won't be in
            # track_map.  Fall back to loading directly from source_dir.
            source_audio: dict[str, tuple] = {}
            for step, filename in enumerate(sorted(needed_sources)):
                self.progress.emit(f"Loading {filename}")
                self.progress_value.emit(step, total)
                track = track_map.get(filename)
                if track and track.audio_data is not None and track.audio_data.size > 0:
                    source_audio[filename] = (track.audio_data, track.samplerate)
                    continue
                # Determine best path to load from
                src_path = None
                if self._source_dir:
                    candidate = os.path.join(self._source_dir, filename)
                    if os.path.isfile(candidate):
                        src_path = candidate
                if src_path is None and track:
                    src_path = track.filepath
                if src_path is None:
                    continue
                loaded = load_track(src_path)
                if track:
                    track.audio_data = loaded.audio_data
                    track.samplerate = loaded.samplerate
                    track.total_samples = loaded.total_samples
                source_audio[filename] = (loaded.audio_data, loaded.samplerate)

            # Phase B: Resolve topology + write output files
            output_tracks = []
            errors = []
            base_step = len(needed_sources)
            for idx, entry in enumerate(topology.entries):
                step = base_step + idx
                self.progress.emit(f"Writing {entry.output_filename}")
                self.progress_value.emit(step, total)

                # Skip entries with no channels (e.g. all channels moved elsewhere)
                if entry.output_channels < 1 or not entry.sources:
                    continue

                try:
                    # Check all sources are available before resolving
                    missing = [
                        s.input_filename for s in entry.sources
                        if s.input_filename not in source_audio
                    ]
                    if missing:
                        errors.append((
                            entry.output_filename,
                            f"missing source(s): {', '.join(missing)}"))
                        continue
                    resolved = resolve_entry_audio(entry, source_audio)
                    # Get format info from first source (track_map or source_audio)
                    src_track = None
                    sr = 44100
                    subtype = "PCM_24"
                    bitdepth = "24-bit"
                    if entry.sources:
                        first_src = entry.sources[0].input_filename
                        src_track = track_map.get(first_src)
                        if src_track:
                            sr = src_track.samplerate
                            subtype = src_track.subtype
                            bitdepth = src_track.bitdepth
                        elif first_src in source_audio:
                            _, sr = source_audio[first_src]

                    dst = os.path.join(output_dir, entry.output_filename)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    sf.write(dst, resolved, sr, subtype=subtype)

                    # Build + save peak cache for this output file
                    if self._peaks_dir:
                        try:
                            from ..waveform.peakcache import (
                                build_peaks, save_peaks, peaks_path_for,
                                get_source_mtime,
                            )
                            log.debug("Building peak cache for applied output '%s'", entry.output_filename)
                            mtime = get_source_mtime(dst)
                            pd = build_peaks(resolved, sr, source_mtime=mtime)
                            pp = peaks_path_for(
                                self._peaks_dir, entry.output_filename)
                            save_peaks(pd, pp)
                            log.debug("Saved peak cache for '%s' (%d levels)", entry.output_filename, len(pd.levels))
                        except Exception as e:
                            log.debug("Failed to build/save peak cache for '%s': %s", entry.output_filename, e)

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
            self.apply_finished.emit()

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
