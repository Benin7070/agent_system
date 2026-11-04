import ctypes
import ctypes.util
import io
import math
import struct
import numpy as np
from typing import Optional, Tuple
from faster_whisper import WhisperModel


def is_cuda_runtime_available() -> bool:
    """Verifies that a CUDA GPU is present AND libcublas is loadable without errors."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return False

        # Check if cublas library can actually be loaded dynamically
        lib = ctypes.util.find_library("cublas") or ctypes.util.find_library("cublas64_12")
        if lib:
            try:
                ctypes.CDLL(lib)
                return True
            except OSError:
                pass

        for soname in ("libcublas.so.12", "libcublas.so.11", "libcublas.so"):
            try:
                ctypes.CDLL(soname)
                return True
            except OSError:
                continue

        return False
    except Exception:
        return False


class SttEngine:
    """Fast, responsive whisper engine tuned for voice commands with zero capture lag."""

    def __init__(
        self,
        model_size: str = "base.en",
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        cpu_threads: int = 2,
    ):
        self.model_size = model_size
        self.cpu_threads = cpu_threads

        if device is None:
            # Check if CUDA runtime libraries (libcublas) are actually present on system
            if is_cuda_runtime_available():
                self.device = "cuda"
                self.compute_type = compute_type or "float16"
            else:
                self.device = "cpu"
                self.compute_type = compute_type or "int8"
        else:
            self.device = device
            self.compute_type = compute_type or ("float16" if device == "cuda" else "int8")

        self._model: Optional[WhisperModel] = None

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            threads = self.cpu_threads if self.device == "cpu" else 0
            try:
                # Load from local cache first — avoids network queries and token prompts
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=threads,
                    local_files_only=True,
                )
            except Exception:
                # Fallback to download if cache is missing
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=threads,
                )
        return self._model

    @staticmethod
    def calculate_rms_intensity(pcm_bytes: bytes) -> float:
        """Calculates normalized audio intensity (0.0 to 1.0) from raw 16-bit PCM."""
        if not pcm_bytes:
            return 0.0
        count = len(pcm_bytes) // 2
        if count == 0:
            return 0.0
        format_str = f"<{count}h"
        try:
            samples = struct.unpack(format_str, pcm_bytes[: count * 2])
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / count)
            # Normalize: 32767 max, typical speech RMS is 1000 - 8000
            intensity = min(1.0, rms / 6000.0)
            return float(intensity)
        except Exception:
            return 0.0

    @staticmethod
    def preprocess_audio(pcm_bytes: bytes) -> np.ndarray:
        """Converts PCM bytes to float32, removes DC offset, and normalizes speech amplitude."""
        if not pcm_bytes:
            return np.array([], dtype=np.float32)
        audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if len(audio_array) == 0:
            return audio_array

        # 1. DC offset removal (centers waveform around 0.0)
        audio_array = audio_array - np.mean(audio_array)

        # 2. Dynamic peak normalization for low-volume microphone captures
        max_val = np.max(np.abs(audio_array))
        if 0.02 < max_val < 0.4:
            audio_array = audio_array * (0.6 / max_val)

        return audio_array

    def transcribe_bytes(self, pcm_bytes: bytes) -> str:
        """Transcribes raw 16-bit 16kHz mono PCM bytes to text quickly with greedy decoding."""
        if len(pcm_bytes) < 3200:  # less than 100ms
            return ""

        audio_array = self.preprocess_audio(pcm_bytes)

        try:
            # beam_size=1 (greedy) is ~3.5x faster on CPU with excellent accuracy for voice commands
            segments, _info = self.model.transcribe(
                audio_array,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                without_timestamps=True,
                vad_filter=False,
                language="en",
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return text
        except RuntimeError as e:
            if "libcublas" in str(e) or "CUDA" in str(e) or "cuda" in str(e):
                print(f"[STT] CUDA library missing ({e}). Automatically switching to CPU int8...")
                self.device = "cpu"
                self.compute_type = "int8"
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self.cpu_threads,
                    local_files_only=True,
                )
                segments, _info = self.model.transcribe(
                    audio_array,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    without_timestamps=True,
                    vad_filter=False,
                    language="en",
                )
                return " ".join(seg.text.strip() for seg in segments).strip()
            raise
