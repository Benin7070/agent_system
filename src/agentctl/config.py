import os
from pathlib import Path
from agentctl.client.daemon import get_socket_path

# Root directory of the repository
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Orb animation & audio signal settings
ORB_SIZE: int = int(os.environ.get("AGENTCTL_ORB_SIZE", 120))
LEVEL_SEND_RATE_HZ: int = int(os.environ.get("AGENTCTL_LEVEL_RATE", 50))
AUDIO_LEVEL_CUTOFF: float = float(os.environ.get("AGENTCTL_AUDIO_CUTOFF", "0.015"))
SILENCE_CUTOFF_MS: int = int(os.environ.get("AGENTCTL_SILENCE_CUTOFF_MS", "1100"))
FULLSCREEN_OVERLAY: bool = os.environ.get("AGENTCTL_FULLSCREEN", "1") in ("1", "true", "True")
USE_FAKE_LEVEL: bool = os.environ.get("AGENTCTL_FAKE_LEVEL", "0") in ("1", "true", "True")
TARGET_SCREEN: str = os.environ.get("AGENTCTL_SCREEN", "").strip()

# Asset paths
ORB_ASSETS_DIR: Path = Path(os.environ.get("AGENTCTL_ORB_ASSETS", REPO_ROOT / "orb" / "orb_assets"))
ASSETS_DIR: Path = Path(os.environ.get("AGENTCTL_ASSETS", REPO_ROOT / "src" / "agentctl" / "ui" / "assets"))

# Socket & WebSocket configuration
SOCKET_PATH: Path = get_socket_path()

