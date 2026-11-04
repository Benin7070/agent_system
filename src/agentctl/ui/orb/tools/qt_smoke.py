"""Smoke test of the real PyQt6 OrbWidget (needs a working OpenGL 3.3 context).

usage: python qt_smoke.py ASSETS_DIR OUT_PNG
Renders ~1.5 s of a loud 'speaking' orb and saves the last frame. Exit code 0 only if the image shows an orb.
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORB_DIR = HERE.parent
DEFAULT_ASSETS = ORB_DIR / "assets"

assets = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_ASSETS

from PyQt6.QtWidgets import QApplication

try:
    from agentctl.ui.orb.widget import OrbWidget
except ImportError:
    sys.path.insert(0, str(ORB_DIR))
    from widget import OrbWidget  # type: ignore # noqa: E402

OrbWidget.configure_format()
app = QApplication(sys.argv)
orb = OrbWidget(assets, orb_size=300)
orb.show()
orb.set_state("speaking")
end = time.time() + 1.5
while time.time() < end:
    orb.set_level(0.9)
    app.processEvents()
    time.sleep(0.01)
img = orb.grabFramebuffer()
img.save(sys.argv[2])
w, h = img.width(), img.height()
center = img.pixelColor(w // 2, h // 2)
edge = img.pixelColor(2, 2)
print(f"framebuffer {w}x{h}; centre rgba=({center.red()},{center.green()},{center.blue()},{center.alpha()}) corner alpha={edge.alpha()}")
sys.exit(0 if center.alpha() > 200 and edge.alpha() < 10 else 1)
