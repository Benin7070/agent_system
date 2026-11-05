"""Kokoro neural TTS integration package for AgentCTL."""

from agentctl.tts.engine import KokoroTtsEngine
from agentctl.tts.player import TtsPlaybackWorker

__all__ = ["KokoroTtsEngine", "TtsPlaybackWorker"]

