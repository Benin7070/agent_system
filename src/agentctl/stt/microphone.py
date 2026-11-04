import queue
import threading
from typing import Callable, Optional
import sounddevice as sd


class MicrophoneStream:
    """Captures microphone audio at 16kHz mono 16-bit PCM in 20ms frames."""

    def __init__(self, sample_rate: int = 16000, frame_duration_ms: int = 20):
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.blocksize = int(sample_rate * (frame_duration_ms / 1000.0))
        self.queue: queue.Queue = queue.Queue()
        self.stream: Optional[sd.RawInputStream] = None
        self._running = False

    def _callback(self, indata, frames, time_info, status):
        if status:
            pass
        self.queue.put(bytes(indata))

    def start(self):
        if self._running:
            return
        self._running = True
        self.stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        self._running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def read_frame(self, timeout: float = 0.1) -> Optional[bytes]:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

