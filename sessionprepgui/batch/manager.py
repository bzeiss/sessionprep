from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from sessionpreplib.models import SessionContext
from sessionpreplib.daw_processor import DawProcessor
from sessionpreplib.daw_processors import create_runtime_daw_processors
from sessionprepgui.analysis.worker import DawCheckWorker, DawTransferWorker

from .panel import BatchItem

class BatchManager(QObject):
    """Orchestrates DAW check and sequential transfer workers for batch processing."""

    # Emitted when all jobs are complete (or single job finishes)
    finished = Signal()
    # Emitted when a single item finishes (item_id, status, result_text)
    item_finished = Signal(str, str, str)
    # Emitted when a batch (or single item) starts
    started = Signal()
    # Emitted to update overall progress (current, total)
    batch_progress_value = Signal(int, int)
    # Emitted to update status bar message
    batch_progress_message = Signal(str)

    def __init__(self, main_window: Any):
        super().__init__()
        self._main_window = main_window
        self._queue: list[BatchItem] = []
        self._current_index: int = 0
        self._running: bool = False

        # Workers
        self._check_worker: DawCheckWorker | None = None
        self._transfer_worker: DawTransferWorker | None = None
        self._current_dp: DawProcessor | None = None
        self._current_session: SessionContext | None = None
        self._is_single_job: bool = False

    def start_batch(self, items: list[BatchItem]):
        if self._running or not items:
            return

        self._queue = items
        self._current_index = 0
        self._running = True
        self._is_single_job = False

        self.started.emit()
        self._process_next()

    def start_single(self, item: BatchItem):
        if self._running:
            return

        self._queue = [item]
        self._current_index = 0
        self._running = True
        self._is_single_job = True

        self.started.emit()
        self._process_next()

    def _process_next(self):
        if self._current_index >= len(self._queue):
            self._finish_batch()
            return

        # Emit baseline progress for this job
        self._on_transfer_progress_value(0, 1)

        item = self._queue[self._current_index]
        item.status = "Running"
        item.result_text = "Checking DAW..."
        self.item_finished.emit(item.id, item.status, item.result_text)
        self.batch_progress_message.emit(f"[{item.project_name}] Checking DAW...")

        # 1. Rehydrate Session and Processor
        try:
            self._current_session = self._rehydrate_session(item.session_state)
            self._current_dp = self._get_daw_processor(item.daw_processor_id, self._current_session.config)
        except Exception as e:
            self._handle_item_failure(item, f"Failed to prepare job: {e}")
            return

        if not self._current_dp:
            self._handle_item_failure(item, f"DAW Processor '{item.daw_processor_id}' not found.")
            return

        # 2. Run Pre-flight Check (connectivity & open session)
        self._check_worker = DawCheckWorker(self._current_dp)
        self._check_worker.result.connect(lambda ok, msg: self._on_check_result(item, ok, msg))
        self._check_worker.start()

    @Slot(object, bool, str)
    def _on_check_result(self, item: BatchItem, ok: bool, message: str):
        self._check_worker = None

        if not ok:
            self._handle_item_failure(item, f"DAW Check Failed: {message}")
            return

        # Optional: Further checks against the DAW state could be placed here if needed.
        # e.g., if message indicates a session is already open and shouldn't be.
        # We rely on the fetch/check logic for now.
        if "PRO_TOOLS_SESSION_OPEN" in message:
            self._handle_item_failure(item, "DAW Check Failed: Pro Tools session is open. Close it first.")
            return

        # 3. Start Transfer
        item.result_text = "Transferring..."
        self.item_finished.emit(item.id, item.status, item.result_text)
        self.batch_progress_message.emit(f"[{item.project_name}] Transferring...")

        self._transfer_worker = DawTransferWorker(
            self._current_dp, self._current_session, item.output_path)
        self._transfer_worker.progress.connect(self._on_transfer_progress)
        self._transfer_worker.progress_value.connect(self._on_transfer_progress_value)
        self._transfer_worker.result.connect(lambda ok, msg, results: self._on_transfer_result(item, ok, msg))
        self._transfer_worker.start()

    @Slot(str)
    def _on_transfer_progress(self, message: str):
        if self._current_index < len(self._queue):
            item = self._queue[self._current_index]
            self.batch_progress_message.emit(f"[{item.project_name}] {message}")

    @Slot(int, int)
    def _on_transfer_progress_value(self, current: int, total: int):
        fraction = current / total if total > 0 else 0
        if not self._is_single_job:
            overall_total = len(self._queue) * 100
            overall_current = int((self._current_index * 100) + (fraction * 100))
        else:
            overall_total = 100
            overall_current = int(fraction * 100)

        self.batch_progress_value.emit(overall_current, overall_total)

    @Slot(object, bool, str)
    def _on_transfer_result(self, item: BatchItem, ok: bool, message: str):
        self._transfer_worker = None

        if ok:
            item.status = "Success"
            item.result_text = "Success"
        else:
            item.status = "Failed"
            item.result_text = message

        self.item_finished.emit(item.id, item.status, item.result_text)

        self._current_index += 1
        self._process_next()

    def _handle_item_failure(self, item: BatchItem, error_msg: str):
        item.status = "Failed"
        item.result_text = error_msg
        self.item_finished.emit(item.id, item.status, item.result_text)

        # In a batch, we proceed to next item even if one fails
        self._current_index += 1
        self._process_next()

    def _finish_batch(self):
        self._running = False
        self._current_index = 0
        self._queue = []
        self._current_dp = None
        self._current_session = None
        self.finished.emit()

    def _rehydrate_session(self, state_dict: dict[str, Any]) -> SessionContext:
        """Create a SessionContext instance from the captured dictionary state."""
        from sessionpreplib.models import SessionContext
        from sessionpreplib.detectors import default_detectors
        from sessionpreplib.processors import default_processors
        from sessionpreplib.daw_processors import default_daw_processors
        from sessionpreplib.config import default_config

        tracks = state_dict.get("tracks", [])
        source_dir = state_dict.get("source_dir", "")

        flat_config = dict(default_config())

        # Inject defaults for all components so that toggles like protools_enabled exist
        for det in default_detectors():
            for param in getattr(det.__class__, "config_params", lambda: [])():
                flat_config[param.key] = param.default
        for proc in default_processors():
            for param in getattr(proc.__class__, "config_params", lambda: [])():
                flat_config[param.key] = param.default
        for dp in default_daw_processors():
            for param in getattr(dp.__class__, "config_params", lambda: [])():
                flat_config[param.key] = param.default

        if state_dict.get("session_config"):
            from sessionpreplib.config import flatten_structured_config
            flat_config.update(flatten_structured_config(state_dict["session_config"]))

        # Re-inject the saved groups and colors for the DAW processor to use
        # (This matches what _do_daw_transfer does before calling the worker)
        flat_config.setdefault("gui", {})["groups"] = state_dict.get("session_groups", [])

        # Colors must come from the global config. The manager receives the main window reference,
        # so we can fetch the active global colors from it.
        from sessionprepgui.theme import PT_DEFAULT_COLORS
        if self._main_window and hasattr(self._main_window, "_config"):
            colors = self._main_window._config.get("colors", PT_DEFAULT_COLORS)
        else:
            colors = PT_DEFAULT_COLORS
        flat_config["gui"]["colors"] = colors

        flat_config["_source_dir"] = source_dir

        all_detectors = default_detectors()
        for d in all_detectors:
            d.configure(flat_config)

        all_processors = []
        for proc in default_processors():
            proc.configure(flat_config)
            if proc.enabled:
                all_processors.append(proc)
        all_processors.sort(key=lambda p: p.priority)

        session = SessionContext(
            tracks=tracks,
            config=flat_config,
            groups={},
            detectors=all_detectors,
            processors=all_processors,
            daw_state=state_dict.get("daw_state", {}),
            prepare_state=state_dict.get("prepare_state", "none"),
            transfer_manifest=state_dict.get("transfer_manifest", []),
            base_transfer_manifest=state_dict.get("base_transfer_manifest", []),
            project_name=state_dict.get("project_name", ""),
        )
        # Assuming topology and topology_applied are needed we could restore them too,
        # but for transfer, `transfer_manifest` and `output_tracks` (which we rebuilt during load) are key.
        # Restore output_tracks directly from the state dict (added in v6 format)
        # Rebuilding from topology would lose processor_results (e.g. fader_offset)
        session.output_tracks = state_dict.get("output_tracks", [])

        return session

    def _get_daw_processor(self, dp_id: str, flat_config: dict[str, Any]) -> DawProcessor | None:
        processors = create_runtime_daw_processors(flat_config)
        for dp in processors:
            if dp.id == dp_id:
                return dp
        return None
