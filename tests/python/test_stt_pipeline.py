import math
import struct
import pytest
from agentctl.stt.engine import SttEngine
from agentctl.stt.vad import SilenceAccumulator


def generate_pcm_sine(freq: float = 440.0, duration_ms: int = 20, sample_rate: int = 16000, amp: float = 0.5) -> bytes:
    """Generates 16-bit PCM sine wave frame."""
    num_samples = int(sample_rate * (duration_ms / 1000.0))
    raw = bytearray()
    for i in range(num_samples):
        val = int(amp * 32767.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
        raw.extend(struct.pack("<h", val))
    return bytes(raw)


def test_rms_intensity_calculation():
    # Silent frame
    silent_frame = bytes(640)
    assert SttEngine.calculate_rms_intensity(silent_frame) == 0.0

    # Sine wave frame
    loud_frame = generate_pcm_sine(freq=300.0, duration_ms=20, amp=0.8)
    intensity = SttEngine.calculate_rms_intensity(loud_frame)
    assert intensity > 0.1
    assert intensity <= 1.0


def test_silence_accumulator_trigger():
    accumulator = SilenceAccumulator(
        sample_rate=16000,
        frame_duration_ms=20,
        silence_threshold_ms=100,
        min_speech_frames=2,
    )
    speech_frame = generate_pcm_sine(freq=250.0, duration_ms=20, amp=0.6)
    silent_frame = bytes(640)

    # Before speech
    assert not accumulator.process_frame(silent_frame)

    # Speech frames (user speaking for several frames)
    for _ in range(5):
        accumulator.process_frame(speech_frame)
    assert accumulator.has_spoken

    # Feed silent frames until silence threshold triggers
    triggered = False
    for _ in range(15):
        if accumulator.process_frame(silent_frame):
            triggered = True
            break

    assert triggered, "Accumulator must trigger after enough consecutive silence"
    assert len(accumulator.get_speech_audio_bytes()) > 0


def test_delta_speech_gating():
    accumulator = SilenceAccumulator(
        sample_rate=16000,
        frame_duration_ms=20,
        silence_threshold_ms=1000,
        min_speech_frames=2,
    )
    speech_frame = generate_pcm_sine(freq=300.0, duration_ms=20, amp=0.7)
    silent_frame = bytes(640)

    # 1. Before speaking: should never trigger partial
    assert not accumulator.should_trigger_partial(min_new_speech_ms=100)

    # 2. Feed 2 silent frames: still false
    accumulator.process_frame(silent_frame)
    accumulator.process_frame(silent_frame)
    assert not accumulator.should_trigger_partial(min_new_speech_ms=100)

    # 3. Speak for 6 frames (120ms): should trigger partial for min_new_speech_ms=100
    for _ in range(6):
        accumulator.process_frame(speech_frame)
    assert accumulator.should_trigger_partial(min_new_speech_ms=100)

    # 4. Immediate second check should return False (delta was reset to 0)
    assert not accumulator.should_trigger_partial(min_new_speech_ms=100)

    # 5. During subsequent silence, should NOT trigger partial
    for _ in range(10):
        accumulator.process_frame(silent_frame)
    assert not accumulator.should_trigger_partial(min_new_speech_ms=100)


def test_tail_clipping():
    accumulator = SilenceAccumulator(
        sample_rate=16000,
        frame_duration_ms=20,
        silence_threshold_ms=1000,
        min_speech_frames=2,
    )
    speech_frame = generate_pcm_sine(freq=300.0, duration_ms=20, amp=0.7)
    silent_frame = bytes(640)

    # Speak for 5 frames
    for _ in range(5):
        accumulator.process_frame(speech_frame)

    raw_len = len(accumulator.speech_frames)

    # Feed 20 silent frames (400ms of silence)
    for _ in range(20):
        accumulator.process_frame(silent_frame)

    # In speech_frames: 5 speech + 20 silent = 25 frames
    total_frames = len(accumulator.speech_frames)
    assert total_frames > 20

    # get_speech_audio_bytes should keep at most 8 frames (160ms) of tail silence
    clipped_bytes = accumulator.get_speech_audio_bytes()
    clipped_frame_count = len(clipped_bytes) // 640

    # 5 speech frames + 8 tail frames = 13 frames (substantially clipped from 25+ frames)
    assert clipped_frame_count < total_frames
    assert clipped_frame_count == len(accumulator.pre_speech_ring) + 5 + 8


def test_audio_preprocessing():
    # Empty input
    assert len(SttEngine.preprocess_audio(b"")) == 0

    # Test DC offset removal: add +5000 offset to raw samples
    num_samples = 320
    offset = 5000
    raw = bytearray()
    for _ in range(num_samples):
        raw.extend(struct.pack("<h", offset))

    processed = SttEngine.preprocess_audio(bytes(raw))
    # DC offset should be centered at 0.0
    assert abs(float(processed.mean())) < 1e-4

    # Test dynamic peak normalization on quiet audio (e.g. amp 0.05)
    quiet_frame = generate_pcm_sine(freq=300.0, duration_ms=20, amp=0.05)
    normalized = SttEngine.preprocess_audio(quiet_frame)
    # Peak should be scaled up towards 0.6
    peak = float(abs(normalized).max())
    assert 0.5 <= peak <= 0.7
