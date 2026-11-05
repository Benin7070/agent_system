import os
from pathlib import Path
from agentctl.client.daemon import get_socket_path

# Root directory of the repository
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

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


