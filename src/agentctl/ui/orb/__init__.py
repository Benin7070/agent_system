"""Native GPU-accelerated voice assistant orb widget & motion controller."""
from pathlib import Path

from agentctl.ui.orb.motion import OrbMotion
from agentctl.ui.orb.widget import OrbWidget

ORB_ASSETS_DIR: Path = Path(__file__).resolve().parent / "assets"

__all__ = ["OrbWidget", "OrbMotion", "ORB_ASSETS_DIR"]
