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
        self._paused = False

    def _callback(self, indata, frames, time_info, status):
        if status:
            pass
        if not self._paused:
            self.queue.put(bytes(indata))

    def pause(self):
        """Temporarily pauses capturing (e.g. while assistant is speaking)."""
        self._paused = True
        self.flush()

    def resume(self):
        """Resumes capturing after speech playback completes."""
        self.flush()
        self._paused = False

    def flush(self):
        """Flushes any accumulated audio frames in the queue."""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break

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

