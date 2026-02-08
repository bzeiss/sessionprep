"""Background worker thread for pipeline analysis."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from sessionpreplib.pipeline import Pipeline, load_session
from sessionpreplib.detectors import default_detectors
from sessionpreplib.processors import default_processors
from sessionpreplib.rendering import build_diagnostic_summary
from sessionpreplib.events import EventBus


class AnalyzeWorker(QThread):
    """Runs pipeline analysis in a background thread."""

    progress = Signal(str)
    finished = Signal(object, object)  # (session, diagnostic_summary)
    error = Signal(str)

    def __init__(self, source_dir: str, config: dict):
        super().__init__()
        self.source_dir = source_dir
        self.config = config

    def run(self):
        try:
            event_bus = EventBus()

            self.progress.emit("Loading session...")
            session = load_session(self.source_dir, self.config, event_bus=event_bus)

            if not session.tracks:
                self.error.emit("No .wav files found in directory.")
                return

            self.progress.emit("Building pipeline...")
            pipeline = Pipeline(
                detectors=default_detectors(),
                audio_processors=default_processors(),
                config=self.config,
                event_bus=event_bus,
            )

            self.progress.emit("Analyzing...")
            session = pipeline.analyze(session)

            self.progress.emit("Planning...")
            session = pipeline.plan(session)

            self.progress.emit("Building summary...")
            summary = build_diagnostic_summary(session)

            self.finished.emit(session, summary)
        except Exception as e:
            self.error.emit(str(e))
