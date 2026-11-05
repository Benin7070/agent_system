import os
from pathlib import Path
from agentctl.client.daemon import get_socket_path

# Root directory of the repository
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv() -> None:
    candidate_paths = [
        REPO_ROOT / ".env",
        Path.cwd() / ".env",
    ]
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        candidate_paths.append(Path(f"/home/{sudo_user}/.config/agentctl/.env"))
        candidate_paths.append(Path(f"/home/{sudo_user}/Programming/agents/.env"))
    candidate_paths.append(Path.home() / ".config" / "agentctl" / ".env")

    for path in candidate_paths:
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            key, _, val = line.partition("=")
                            key = key.strip()
                            val = val.strip()
                            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                                val = val[1:-1]
                            if key and (key not in os.environ or not os.environ[key]):
                                os.environ[key] = val
                break
            except Exception:
                pass


_load_dotenv()

# Orb animation & audio signal settings
ORB_SIZE: int = int(os.environ.get("AGENTCTL_ORB_SIZE", 120))
LEVEL_SEND_RATE_HZ: int = int(os.environ.get("AGENTCTL_LEVEL_RATE", 50))
AUDIO_LEVEL_CUTOFF: float = float(os.environ.get("AGENTCTL_AUDIO_CUTOFF", "0.015"))
SILENCE_CUTOFF_MS: int = int(os.environ.get("AGENTCTL_SILENCE_CUTOFF_MS", "1750"))
FULLSCREEN_OVERLAY: bool = os.environ.get("AGENTCTL_FULLSCREEN", "1") in ("1", "true", "True")
USE_FAKE_LEVEL: bool = os.environ.get("AGENTCTL_FAKE_LEVEL", "0") in ("1", "true", "True")
TARGET_SCREEN: str = os.environ.get("AGENTCTL_SCREEN", "").strip()

# Asset paths
ORB_DIR: Path = REPO_ROOT / "src" / "agentctl" / "ui" / "orb"
ORB_ASSETS_DIR: Path = Path(os.environ.get("AGENTCTL_ORB_ASSETS", ORB_DIR / "assets"))
ASSETS_DIR: Path = Path(os.environ.get("AGENTCTL_ASSETS", REPO_ROOT / "src" / "agentctl" / "ui" / "assets"))

# Socket & WebSocket configuration
SOCKET_PATH: Path = get_socket_path()


# Edge glow settings
class EdgeGlowConfig:
    def __init__(self):
        self._enabled: bool | None = None
        self._band_px: float | None = None
        self._idle_intensity: float | None = None
        self._corner_radius_px: float | None = None
        self._screens: str | None = None

    @property
    def enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        return os.environ.get("AGENTCTL_EDGE_GLOW_ENABLED", "1").lower() in ("1", "true", "yes", "on")

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    @property
    def band_px(self) -> float:
        if self._band_px is not None:
            return self._band_px
        return float(os.environ.get("AGENTCTL_EDGE_GLOW_BAND_PX", "80"))

    @band_px.setter
    def band_px(self, value: float) -> None:
        self._band_px = float(value)

    @property
    def idle_intensity(self) -> float:
        if self._idle_intensity is not None:
            return self._idle_intensity
        return float(os.environ.get("AGENTCTL_EDGE_GLOW_IDLE_INTENSITY", "0.0"))

    @idle_intensity.setter
    def idle_intensity(self, value: float) -> None:
        self._idle_intensity = float(value)

    @property
    def corner_radius_px(self) -> float:
        if self._corner_radius_px is not None:
            return self._corner_radius_px
        return float(os.environ.get("AGENTCTL_EDGE_GLOW_CORNER_RADIUS_PX", "28"))

    @corner_radius_px.setter
    def corner_radius_px(self, value: float) -> None:
        self._corner_radius_px = float(value)

    @property
    def screens(self) -> str:
        if self._screens is not None:
            return self._screens
        return os.environ.get("AGENTCTL_EDGE_GLOW_SCREENS", "all").strip().lower()

    @screens.setter
    def screens(self, value: str) -> None:
        self._screens = str(value)


edge_glow = EdgeGlowConfig()


# Harness, LLM, TTS, and Session Configurations
class HarnessConfig:
    def __init__(self):
        self._agent_name: str | None = None
        self._user_name: str | None = None

    @property
    def agent_name(self) -> str:
        if self._agent_name is not None:
            return self._agent_name
        return os.environ.get("AGENTCTL_AGENT_NAME", "Bella").strip()

    @agent_name.setter
    def agent_name(self, value: str) -> None:
        self._agent_name = str(value)

    @property
    def user_name(self) -> str:
        if self._user_name is not None:
            return self._user_name
        return os.environ.get("AGENTCTL_USER_NAME") or os.environ.get("SUDO_USER") or os.environ.get("USER") or "Benin"

    @user_name.setter
    def user_name(self, value: str) -> None:
        self._user_name = str(value)


class LlmConfig:
    def __init__(self):
        self._model: str | None = None

    @property
    def model(self) -> str:
        if self._model is not None:
            return self._model
        return os.environ.get("AGENTCTL_LLM_MODEL", "gpt-4o-mini").strip()

    @model.setter
    def model(self, value: str) -> None:
        self._model = str(value)


class TtsConfig:
    def __init__(self):
        self._voice: str | None = None
        self._speed: float | None = None
        self._device: str | None = None
        self._models_dir: Path | None = None
        self._tail_delay_ms: int | None = None

    @property
    def voice(self) -> str:
        if self._voice is not None:
            return self._voice
        return os.environ.get("AGENTCTL_TTS_VOICE", "af_bella").strip()

    @voice.setter
    def voice(self, value: str) -> None:
        self._voice = str(value)

    @property
    def speed(self) -> float:
        if self._speed is not None:
            return self._speed
        try:
            val = float(os.environ.get("AGENTCTL_TTS_SPEED", "1.0"))
            return max(0.8, min(1.2, val))
        except ValueError:
            return 1.0

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.8, min(1.2, float(value)))

    @property
    def device(self) -> str | None:
        if self._device is not None:
            return self._device
        dev = os.environ.get("AGENTCTL_TTS_DEVICE", "").strip()
        return dev if dev else None

    @device.setter
    def device(self, value: str | None) -> None:
        self._device = value

    @property
    def models_dir(self) -> Path:
        if self._models_dir is not None:
            return self._models_dir
        custom = os.environ.get("AGENTCTL_TTS_MODELS_DIR")
        if custom:
            return Path(custom).expanduser()
        return Path(os.path.expanduser("~/.local/share/agentctl/models/kokoro"))

    @models_dir.setter
    def models_dir(self, value: str | Path) -> None:
        self._models_dir = Path(value).expanduser()

    @property
    def tail_delay_ms(self) -> int:
        if self._tail_delay_ms is not None:
            return self._tail_delay_ms
        try:
            return max(0, int(os.environ.get("AGENTCTL_TAIL_DELAY_MS", "250")))
        except ValueError:
            return 250

    @tail_delay_ms.setter
    def tail_delay_ms(self, value: int) -> None:
        self._tail_delay_ms = max(0, int(value))


class SessionConfig:
    def __init__(self):
        self._idle_timeout_s: float | None = None

    @property
    def idle_timeout_s(self) -> float | None:
        if self._idle_timeout_s is not None:
            return self._idle_timeout_s
        raw = os.environ.get("AGENTCTL_SESSION_IDLE_TIMEOUT_S")
        if raw:
            try:
                return float(raw)
            except ValueError:
                return None
        return None

    @idle_timeout_s.setter
    def idle_timeout_s(self, value: float | None) -> None:
        self._idle_timeout_s = float(value) if value is not None else None


harness = HarnessConfig()
llm = LlmConfig()
tts = TtsConfig()
session = SessionConfig()



