"""Audio playback controller using sounddevice."""

from __future__ import annotations

import sounddevice as sd

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class PlaybackController(QObject):
    """Manages audio playback state and sounddevice OutputStream lifecycle.

    Signals:
        cursor_updated(int): Emitted ~30fps with the current sample position.
        playback_finished(): Emitted when playback reaches the end of audio.
        error(str): Emitted on playback errors.
    """

    cursor_updated = Signal(int)
    playback_finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream: sd.OutputStream | None = None
        self._play_start_sample: int = 0
        self._play_frame_count: list[int] = [0]
        self._audio_data = None  # numpy array (samples, channels)
        self._samplerate: int = 44100

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._on_timer)

    @property
    def is_playing(self) -> bool:
        return self._stream is not None and self._stream.active

    @property
    def play_start_sample(self) -> int:
        return self._play_start_sample

    def play(self, audio_data, samplerate: int, start_sample: int = 0,
             mode: str = "as_is", channel: int | None = None):
        """Start playback from the given sample position.

        Parameters
        ----------
        mode : str
            ``"as_is"`` — play all channels unchanged.
            ``"mono"`` — fold all channels to mono (sum / N).
            ``"channel_as_is"`` — solo *channel* in its original speaker
            position (other channels silenced).
            ``"channel_mono"`` — extract *channel* as a 1-channel stream
            routed equally to all speakers.
        channel : int or None
            Channel index for ``channel_as_is`` / ``channel_mono`` modes.
        """
        self.stop()

        if audio_data is None or audio_data.size == 0:
            return

        import numpy as np
        audio = audio_data
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)

        n_ch = audio.shape[1]

        if mode == "mono":
            # Fold all channels to mono: sum / N
            audio = np.mean(audio, axis=1, keepdims=True)
        elif mode == "channel_as_is" and channel is not None and channel < n_ch:
            # Solo channel in its original speaker position
            out = np.zeros_like(audio)
            out[:, channel] = audio[:, channel]
            audio = out
        elif mode == "channel_mono" and channel is not None and channel < n_ch:
            # Extract single channel as mono (1-ch output)
            audio = audio[:, channel:channel + 1].copy()

        # Downmix to stereo if the device can't handle the channel count
        if audio.shape[1] > 2:
            n = audio.shape[1]
            left = np.zeros(audio.shape[0], dtype=audio.dtype)
            right = np.zeros(audio.shape[0], dtype=audio.dtype)
            for ch in range(n):
                if ch % 2 == 0:
                    left += audio[:, ch]
                else:
                    right += audio[:, ch]
            left /= max(1, (n + 1) // 2)
            right /= max(1, n // 2)
            audio = np.column_stack([left, right])

        if start_sample >= audio.shape[0]:
            start_sample = 0

        self._audio_data = audio
        self._samplerate = samplerate
        self._play_start_sample = start_sample
        self._play_frame_count = [0]
        play_data = audio[start_sample:]

        frame_count = self._play_frame_count

        def callback(outdata, frames, time_info, status):
            pos = frame_count[0]
            end = pos + frames
            if end <= len(play_data):
                outdata[:] = play_data[pos:end]
                frame_count[0] = end
            else:
                remaining = len(play_data) - pos
                if remaining > 0:
                    outdata[:remaining] = play_data[pos:]
                outdata[remaining:] = 0
                frame_count[0] = len(play_data)
                raise sd.CallbackStop()

        try:
            self._stream = sd.OutputStream(
                samplerate=samplerate,
                channels=audio.shape[1],
                callback=callback,
                finished_callback=self._on_finished_sd,
            )
            self._stream.start()
            self._timer.start()
        except Exception as e:
            self._stream = None
            self.error.emit(str(e))

    def stop(self):
        """Stop playback. Returns the start sample for cursor restoration."""
        self._timer.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def current_sample(self) -> int:
        """Return the current playback sample position."""
        return self._play_start_sample + self._play_frame_count[0]

    def _on_finished_sd(self):
        """Called by sounddevice from the audio thread when playback ends."""
        QTimer.singleShot(0, self._on_finished_main)

    @Slot()
    def _on_finished_main(self):
        """Handle playback completion on the main thread."""
        self._timer.stop()
        self._stream = None
        self.playback_finished.emit()

    @Slot()
    def _on_timer(self):
        """Emit cursor position updates during playback."""
        if self._stream is not None:
            pos = self._play_start_sample + self._play_frame_count[0]
            self.cursor_updated.emit(pos)
