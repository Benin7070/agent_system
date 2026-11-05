"""Unit tests for Kokoro TTS, streaming player, and microphone muting coordination."""

import time
import numpy as np
import pytest
from unittest.mock import MagicMock

from agentctl.config import (
    harness as harness_config,
    llm as llm_config,
    tts as tts_config,
    session as session_config,
)
from agentctl.stt.microphone import MicrophoneStream
from agentctl.tts.engine import KokoroTtsEngine
from agentctl.tts.player import TtsPlaybackWorker


def test_tts_config_defaults():
    assert len(harness_config.agent_name) > 0
    assert len(harness_config.user_name) > 0
    assert llm_config.model == "gpt-4o-mini"
    assert tts_config.voice == "af_bella"
    assert tts_config.speed == 1.0
    assert tts_config.tail_delay_ms == 250
    assert session_config.idle_timeout_s is None


def test_tts_config_overrides(monkeypatch):
    monkeypatch.setenv("AGENTCTL_AGENT_NAME", "Jarvis")
    monkeypatch.setenv("AGENTCTL_USER_NAME", "Alice")
    monkeypatch.setenv("AGENTCTL_LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("AGENTCTL_TTS_VOICE", "af_bella")
    monkeypatch.setenv("AGENTCTL_TTS_SPEED", "1.1")
    monkeypatch.setenv("AGENTCTL_TAIL_DELAY_MS", "300")
    monkeypatch.setenv("AGENTCTL_SESSION_IDLE_TIMEOUT_S", "45.0")

    # Force re-read
    harness_config._agent_name = None
    harness_config._user_name = None
    llm_config._model = None
    tts_config._voice = None
    tts_config._speed = None
    tts_config._tail_delay_ms = None
    session_config._idle_timeout_s = None

    assert harness_config.agent_name == "Jarvis"
    assert harness_config.user_name == "Alice"
    assert llm_config.model == "gpt-4o"
    assert tts_config.voice == "af_bella"
    assert tts_config.speed == 1.1
    assert tts_config.tail_delay_ms == 300
    assert session_config.idle_timeout_s == 45.0


def test_kokoro_tts_engine_synthesizes_audio():
    engine = KokoroTtsEngine(voice="af_bella", speed=1.0)
    samples, sr = engine.synthesize("Hello Benin! This is a test.")

    assert isinstance(samples, np.ndarray)
    assert samples.dtype == np.float32
    assert sr == 24000
    assert len(samples) > 0
    assert len(samples) / sr > 0.5  # At least 0.5s audio


def test_kokoro_fixed_lines_cached():
    engine = KokoroTtsEngine(voice="af_bella", speed=1.0)
    for key in ("goodbye", "cant_reach_brain", "didnt_catch_that", "no_api_key"):
        cached = engine.get_cached(key)
        assert cached is not None, f"Fixed line '{key}' must be pre-cached at startup"
        samples, sr = cached
        assert len(samples) > 0
        assert sr == 24000


def test_mic_pause_flush_and_resume():
    mic = MicrophoneStream()
    # Mock sounddevice stream
    mic._running = True
    mic._callback(b"\x01\x00" * 320, 320, None, 0)
    assert not mic.queue.empty()

    # Pause drops frames and flushes
    mic.pause()
    assert mic.queue.empty()
    mic._callback(b"\x01\x00" * 320, 320, None, 0)
    assert mic.queue.empty(), "Frames while paused must be dropped"

    # Resume allows capturing frames again
    mic.resume()
    mic._callback(b"\x02\x00" * 320, 320, None, 0)
    assert not mic.queue.empty(), "Frames after resume must be captured"


def test_tts_playback_worker_levels_and_tail_delay():
    levels = []
    finished_called = []

    mock_mic = MagicMock()
    mock_mic.pause = MagicMock()
    mock_mic.resume = MagicMock()

    player = TtsPlaybackWorker(
        mic_stream=mock_mic,
        on_level=lambda lvl: levels.append(lvl),
        on_finished=lambda: finished_called.append(True),
        tail_delay_ms=50,  # 50ms for fast test
    )
    player.start()

    # Synthetic 0.2s 440Hz sine wave at 24kHz
    sr = 24000
    t = np.linspace(0, 0.2, int(sr * 0.2), dtype=np.float32)
    audio = 0.5 * np.sin(2 * np.pi * 440 * t)

    player.enqueue_sentence(audio, sr, is_final=True)

    # Wait for playback to complete
    t0 = time.time()
    while not finished_called and time.time() - t0 < 3.0:
        time.sleep(0.05)

    player.stop()

    assert mock_mic.pause.called, "Microphone must be paused during playback"
    assert mock_mic.resume.called, "Microphone must be resumed after playback + tail delay"
    assert len(levels) > 0, "Level callbacks must be emitted during playback"
    assert levels[-1] == 0.0, "Final level callback must be 0.0"
    assert len(finished_called) == 1, "on_finished callback must be triggered"

