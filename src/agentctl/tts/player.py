"""Streaming TTS audio player with live RMS metering and microphone muting."""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Optional, Tuple
import numpy as np
import sounddevice as sd

from agentctl.config import tts as tts_config
from agentctl.logger import logger
from agentctl.stt.microphone import MicrophoneStream


class TtsPlaybackWorker(threading.Thread):
    """Background worker for sequential streaming audio playback with RMS meter feed."""

    def __init__(
        self,
        mic_stream: Optional[MicrophoneStream] = None,
        on_level: Optional[Callable[[float], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        tail_delay_ms: Optional[int] = None,
        output_device: Optional[str] = None,
    ):
        super().__init__(daemon=True)
        self.mic = mic_stream
        self.on_level = on_level
        self.on_finished = on_finished
        self.tail_delay_ms = tail_delay_ms if tail_delay_ms is not None else tts_config.tail_delay_ms
        self.output_device = output_device or tts_config.device

        self._queue: queue.Queue[Tuple[np.ndarray, int, bool]] = queue.Queue()
        self._running = False
        self._is_playing = False
        self._stop_event = threading.Event()

    def enqueue_sentence(self, samples: np.ndarray, sample_rate: int, is_final: bool = False):
        """Enqueues a synthesized sentence for streaming playback."""
        self._queue.put((samples, sample_rate, is_final))

    def stop(self):
        self._running = False
        self._stop_event.set()

    def run(self):
        self._running = True
        while self._running:
            try:
                samples, sample_rate, is_final = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            self._play_chunk(samples, sample_rate, is_final)

    def _play_chunk(self, samples: np.ndarray, sample_rate: int, is_final: bool):
        # 1. Mute microphone and flush to prevent self-hearing
        if self.mic and not self._is_playing:
            self.mic.pause()
            self._is_playing = True

        chunk_size = int(sample_rate * 0.033)  # ~33ms slice (~30 Hz update rate)
        total_samples = len(samples)

        try:
            with sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                device=self.output_device,
            ) as stream:
                idx = 0
                while idx < total_samples and not self._stop_event.is_set():
                    end_idx = min(idx + chunk_size, total_samples)
                    slice_data = samples[idx:end_idx]

                    # Measure RMS loudness of this playback slice
                    if len(slice_data) > 0 and self.on_level:
                        rms = float(np.sqrt(np.mean(slice_data ** 2)))
                        # Boost spoken speech RMS curve smoothly into 0..1 range
                        level = max(0.0, min(1.0, rms * 4.0))
                        self.on_level(level)

                    stream.write(slice_data)
                    idx = end_idx

        except Exception as e:
            logger.warn("TTS", f"Audio playback stream error: {e}")

        # If this was the final sentence in the assistant's turn, finish and un-mute
        if is_final and self._queue.empty():
            if self.on_level:
                self.on_level(0.0)

            # Wait for tail delay before re-arming the microphone
            if self.tail_delay_ms > 0:
                time.sleep(self.tail_delay_ms / 1000.0)

            if self.mic:
                self.mic.resume()

            self._is_playing = False

            if self.on_finished:
                self.on_finished()

