"""Kokoro TTS local engine using ONNX runtime CPU execution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Tuple
import numpy as np

from agentctl.config import tts as tts_config
from agentctl.logger import logger


class KokoroTtsEngine:
    """CPU-only local Kokoro neural TTS runner with voice caching."""

    def __init__(
        self,
        voice: Optional[str] = None,
        speed: Optional[float] = None,
        models_dir: Optional[Path] = None,
    ):
        self.voice = voice or tts_config.voice
        self.speed = speed or tts_config.speed
        self.models_dir = Path(models_dir or tts_config.models_dir).expanduser()
        self._kokoro = None
        self._cache: Dict[str, Tuple[np.ndarray, int]] = {}

        self._init_model()
        self._precache_fixed_lines()

    def _init_model(self):
        model_path = self.models_dir / "kokoro-v1.0.onnx"
        voices_path = self.models_dir / "voices-v1.0.bin"

        if not model_path.exists() or not voices_path.exists():
            logger.warn(
                "TTS",
                f"Kokoro model files not found at {self.models_dir}. TTS will run in fallback dummy mode.",
            )
            return

        try:
            from kokoro_onnx import Kokoro
            logger.info("TTS", f"Loading Kokoro ONNX model from {model_path} (voice={self.voice}, speed={self.speed})...")
            self._kokoro = Kokoro(str(model_path), str(voices_path))
            available = self._kokoro.get_voices()
            if self.voice not in available:
                logger.warn("TTS", f"Voice '{self.voice}' not found in voices file. Available: {available[:5]}... Falling back to 'af_bella'")
                self.voice = "af_bella" if "af_bella" in available else available[0]
            logger.success("TTS", f"Kokoro TTS engine ready with voice '{self.voice}'.")
        except Exception as e:
            logger.error("TTS", f"Failed to load Kokoro ONNX engine: {e}")
            self._kokoro = None

    def _precache_fixed_lines(self):
        """Pre-generates and caches fixed prompts for zero-latency response with disk persistence."""
        fixed_lines = {
            "goodbye": "Goodbye!",
            "cant_reach_brain": "Sorry, I can't reach my brain right now.",
            "didnt_catch_that": "Sorry, I didn't catch that.",
            "no_api_key": "No OpenAI API key was found in the environment.",
        }

        if not self._kokoro:
            return

        cache_dir = Path("~/.local/share/agentctl/cache/tts_prompts").expanduser()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        loaded_disk = 0
        for key, text in fixed_lines.items():
            cache_file = cache_dir / f"{self.voice}_{key}.npz"
            if cache_file.exists():
                try:
                    data = np.load(cache_file)
                    samples = data["samples"]
                    sr = int(data["sr"])
                    self._cache[key] = (samples, sr)
                    loaded_disk += 1
                    continue
                except Exception as e:
                    logger.warn("TTS", f"Corrupt prompt cache for '{key}', re-synthesizing: {e}")

            try:
                samples, sr = self.synthesize_direct(text)
                self._cache[key] = (samples, sr)
                try:
                    np.savez_compressed(cache_file, samples=samples, sr=sr)
                except Exception as e:
                    logger.warn("TTS", f"Could not write prompt cache for '{key}': {e}")
            except Exception as e:
                logger.warn("TTS", f"Failed to pre-cache fixed line '{key}': {e}")

        if loaded_disk == len(fixed_lines):
            logger.info("TTS", f"Loaded {len(self._cache)} fixed vocal responses from disk cache (0s delay).")
        else:
            logger.info("TTS", f"Pre-cached {len(self._cache)} fixed vocal responses ({loaded_disk} from disk).")

    def get_cached(self, key: str) -> Optional[Tuple[np.ndarray, int]]:
        return self._cache.get(key)

    def synthesize_direct(self, text: str) -> Tuple[np.ndarray, int]:
        """Synthesizes text directly without checking pre-cache."""
        if not self._kokoro:
            # Fallback dummy audio: 0.5s of gentle sine wave at 24kHz
            sr = 24000
            t = np.linspace(0, 0.5, int(sr * 0.5), dtype=np.float32)
            sine = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            return sine, sr

        samples, sr = self._kokoro.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang="en-us",
        )
        return samples.astype(np.float32), sr

    def synthesize(self, text: str) -> Tuple[np.ndarray, int]:
        """Synthesizes sentence text, checking fixed cache first."""
        cleaned = text.strip()
        for key, (samples, sr) in self._cache.items():
            if cleaned.lower() == key.replace("_", " "):
                return samples, sr

        return self.synthesize_direct(cleaned)

