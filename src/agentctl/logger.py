import datetime
import os
import sys
from pathlib import Path

LOG_DIR = Path.home() / ".local" / "share" / "agentctl"
LOG_FILE = LOG_DIR / "overlay.log"

# ANSI color codes for terminal
COLOR_RESET = "\033[0m"
COLOR_DIM = "\033[2m"
COLOR_CYAN = "\033[36m"
COLOR_GREEN = "\033[32m"
COLOR_YELLOW = "\033[33m"
COLOR_RED = "\033[31m"
COLOR_MAGENTA = "\033[35m"
COLOR_BOLD = "\033[1m"


class AgentLogger:
    """Real-time logger outputting to console and persistent log file."""

    def __init__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = LOG_FILE
        self._last_audio_log_time = 0.0

    def _write(self, tag: str, color: str, message: str):
        now_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        file_line = f"[{now_str}] [{tag:<7}] {message}\n"

        # Console line with color
        if sys.stdout.isatty():
            console_line = f"{COLOR_DIM}[{now_str}]{COLOR_RESET} {color}[{tag:<7}]{COLOR_RESET} {message}"
        else:
            console_line = file_line.rstrip()

        print(console_line, flush=True)

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(file_line)
        except Exception:
            pass

    def info(self, tag: str, message: str):
        self._write(tag, COLOR_CYAN, message)

    def vad(self, message: str):
        self._write("VAD", COLOR_MAGENTA, message)

    def whisper(self, message: str):
        self._write("WHISPER", COLOR_YELLOW, message)

    def audio(self, rms: float, is_speech: bool, has_spoken: bool, consec_silent: int, total_speech: int):
        speech_tag = f"{COLOR_GREEN}SPEECH{COLOR_RESET}" if is_speech else f"{COLOR_DIM}SILENCE{COLOR_RESET}"
        status = f"RMS={rms:.4f} | VAD={speech_tag} | has_spoken={has_spoken} | silent_frames={consec_silent} | speech_frames={total_speech}"
        self._write("AUDIO", COLOR_CYAN, status)

    def success(self, tag: str, message: str):
        self._write(tag, COLOR_GREEN, message)

    def warn(self, tag: str, message: str):
        self._write(tag, COLOR_RED, message)


# Singleton
logger = AgentLogger()

