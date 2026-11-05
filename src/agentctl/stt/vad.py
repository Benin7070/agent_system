import collections
import struct
import math
from typing import Deque, List
import webrtcvad


class VadDetector:
    """Voice Activity Detector with WebRTC VAD, energy thresholding, and noise gating."""

    def __init__(self, sample_rate: int = 16000, frame_duration_ms: int = 20, mode: int = 3):
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = int(sample_rate * (frame_duration_ms / 1000.0) * 2)  # 16-bit PCM = 2 bytes/sample
        self.vad = webrtcvad.Vad(mode)

    @staticmethod
    def calculate_frame_energy(frame_bytes: bytes) -> float:
        """Calculates normalized RMS energy (0.0 to 1.0) of a frame."""
        count = len(frame_bytes) // 2
        if count == 0:
            return 0.0
        try:
            samples = struct.unpack(f"<{count}h", frame_bytes[: count * 2])
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / count)
            return min(1.0, rms / 6000.0)
        except Exception:
            return 0.0

    @staticmethod
    def apply_noise_gate(frame_bytes: bytes, noise_floor: float = 0.006) -> bytes:
        """Damps / zeroes low-level laptop fan hiss and ambient room noise."""
        energy = VadDetector.calculate_frame_energy(frame_bytes)
        if energy < noise_floor:
            return bytes(len(frame_bytes))
        return frame_bytes

    def is_speech(self, frame_bytes: bytes, min_energy: float = 0.04) -> bool:
        if len(frame_bytes) != self.frame_size:
            return False

        # Low-energy frames (fan hiss, room tone, silence) cannot be human speech
        energy = self.calculate_frame_energy(frame_bytes)
        if energy < min_energy:
            return False

        return self.vad.is_speech(frame_bytes, self.sample_rate)


class SilenceAccumulator:
    """Accumulates speech frames with adaptive noise floor calibration, ring buffer, and tail clipping."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration_ms: int = 20,
        silence_threshold_ms: int = 1750,
        pre_speech_buffer_ms: int = 400,
        min_speech_frames: int = 12,  # at least 240ms of speech before silence can trigger
        calibration_frames: int = 0,  # Number of frames to calibrate ambient noise floor (0 = disabled)
    ):
        self.vad = VadDetector(sample_rate, frame_duration_ms, mode=3)
        self.frame_duration_ms = frame_duration_ms
        self.silence_threshold_frames = int(silence_threshold_ms / frame_duration_ms)
        self.min_speech_frames = min_speech_frames
        self.calibration_frames = calibration_frames
        self.calibration_warmup = min(8, calibration_frames // 3) if calibration_frames > 0 else 0
        self.calibration_count = 0
        self.ambient_energy_samples: List[float] = []
        self.dynamic_speech_threshold = 0.045  # default minimum energy

        # Circular buffer to keep the audio right before speech starts (prevents clipping the first word)
        ring_buffer_size = int(pre_speech_buffer_ms / frame_duration_ms)
        self.pre_speech_ring: Deque[bytes] = collections.deque(maxlen=ring_buffer_size)

        self.speech_frames: List[bytes] = []
        self.has_spoken = False
        self.total_speech_frames = 0
        self.consecutive_silent_frames = 0
        self.untranscribed_speech_frames = 0  # Delta tracking: speech frames since last Whisper call
        self.speech_hangover = 0
        self.last_frame_speech = False

    def process_frame(self, frame_bytes: bytes) -> bool:
        """Processes a frame. Returns True only when a full sentence was spoken and silence is detected."""
        gated_frame = VadDetector.apply_noise_gate(frame_bytes)

        # 1. Automatic ambient noise calibration during the first ~400ms of overlay activation
        if self.calibration_count < self.calibration_frames:
            energy = VadDetector.calculate_frame_energy(gated_frame)
            if self.calibration_count >= self.calibration_warmup:
                self.ambient_energy_samples.append(energy)

            self.calibration_count += 1
            if self.calibration_count >= self.calibration_frames:
                if self.ambient_energy_samples:
                    ambient_avg = sum(self.ambient_energy_samples) / len(self.ambient_energy_samples)
                    # Speech must be at least 1.8x louder than ambient noise or >= 0.05
                    self.dynamic_speech_threshold = max(0.05, ambient_avg * 1.8)
            self.pre_speech_ring.append(gated_frame)
            return False

        speech = self.vad.is_speech(gated_frame, min_energy=self.dynamic_speech_threshold)
        self.last_frame_speech = speech

        if not self.has_spoken:
            self.pre_speech_ring.append(gated_frame)
            if speech:
                self.speech_hangover += 1
                # Require 2 consecutive frames or strong energy to confirm intentional speech onset
                if self.speech_hangover >= 2 or VadDetector.calculate_frame_energy(gated_frame) >= self.dynamic_speech_threshold * 1.5:
                    self.has_spoken = True
                    # Prepend the ring buffer so the beginning of the first word is preserved
                    self.speech_frames.extend(self.pre_speech_ring)
                    self.speech_frames.append(gated_frame)
                    self.total_speech_frames += 1
                    self.untranscribed_speech_frames += 1
                    self.consecutive_silent_frames = 0
            else:
                self.speech_hangover = 0
            return False

        # In active speech session
        self.speech_frames.append(gated_frame)

        if speech:
            self.total_speech_frames += 1
            self.speech_hangover += 1
            # Only reset silence counter and increment delta if speech has continuity
            # (filters out isolated single-frame clicks, fan pops, or breathing bursts)
            if self.speech_hangover >= 2:
                self.untranscribed_speech_frames += 1
                self.consecutive_silent_frames = 0
            return False
        else:
            self.speech_hangover = 0
            self.consecutive_silent_frames += 1
            # Check if silence threshold reached
            if self.consecutive_silent_frames >= self.silence_threshold_frames:
                if self.total_speech_frames >= self.min_speech_frames:
                    return True
                else:
                    # False trigger (mic bump, cough, short noise < 240ms): reset back to listening
                    self.reset()
                    return False
            return False

    def should_trigger_partial(self, min_new_speech_ms: int = 700) -> bool:
        """Returns True ONLY if enough NEW speech frames were added. Returns False during silence."""
        min_frames = int(min_new_speech_ms / self.frame_duration_ms)
        if self.untranscribed_speech_frames >= min_frames:
            self.untranscribed_speech_frames = 0
            return True
        return False

    def get_speech_audio_bytes(self) -> bytes:
        """Returns speech audio with trailing silence clipped to cut encoder matrix multiplications."""
        if not self.speech_frames:
            return b""

        # Keep at most 160ms of natural tail silence release, clip the rest (up to 1.2s saved!)
        max_tail_frames = int(160 / self.frame_duration_ms)
        trim_count = max(0, self.consecutive_silent_frames - max_tail_frames)

        if trim_count > 0 and trim_count < len(self.speech_frames):
            clean_frames = self.speech_frames[:-trim_count]
        else:
            clean_frames = self.speech_frames

        return b"".join(clean_frames)

    def reset(self, recalibrate: bool = False):
        self.pre_speech_ring.clear()
        self.speech_frames.clear()
        self.has_spoken = False
        self.total_speech_frames = 0
        self.consecutive_silent_frames = 0
        self.untranscribed_speech_frames = 0
        self.speech_hangover = 0
        if recalibrate:
            self.calibration_count = 0
            self.ambient_energy_samples.clear()
