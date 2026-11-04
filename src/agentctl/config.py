import os
from pathlib import Path
from agentctl.client.daemon import get_socket_path

# Root directory of the repository
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Orb animation settings
ORB_SIZE: int = int(os.environ.get("AGENTCTL_ORB_SIZE", 240))
LEVEL_SEND_RATE_HZ: int = int(os.environ.get("AGENTCTL_LEVEL_RATE", 30))
FULLSCREEN_OVERLAY: bool = os.environ.get("AGENTCTL_FULLSCREEN", "1") in ("1", "true", "True")
USE_FAKE_LEVEL: bool = os.environ.get("AGENTCTL_FAKE_LEVEL", "0") in ("1", "true", "True")

# Asset paths
ORB_ASSETS_DIR: Path = Path(os.environ.get("AGENTCTL_ORB_ASSETS", REPO_ROOT / "orb" / "orb_assets"))

# Socket & WebSocket configuration
SOCKET_PATH: Path = get_socket_path()
