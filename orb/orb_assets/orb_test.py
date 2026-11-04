"""Stand-alone test window: click through every state and drag the level slider. No microphone needed.

    python orb_test.py                 # normal window
    python orb_test.py --transparent   # frameless, transparent, always-on-top (like the real overlay)
    python orb_test.py --size 300      # orb diameter in logical pixels
"""
import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QPushButton, QSlider,
                             QVBoxLayout, QWidget)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from orb_motion import STATES, fake_speech_level  # noqa: E402
from orb_widget import OrbWidget  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default=str(HERE))
    ap.add_argument("--size", type=int, default=260)
    ap.add_argument("--transparent", action="store_true")
    args = ap.parse_args()

    OrbWidget.configure_format()
    app = QApplication(sys.argv)

    orb = OrbWidget(args.assets, orb_size=args.size)
    root = QWidget()
    root.setWindowTitle("Orb test")
    if args.transparent:
        root.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    else:
        root.setStyleSheet("background:#16161c; color:#ddd;")

    lay = QVBoxLayout(root)
    lay.addWidget(orb, alignment=Qt.AlignmentFlag.AlignCenter)

    row = QHBoxLayout()
    for name in STATES:
        b = QPushButton(name)
        b.clicked.connect(lambda _=False, n=name: orb.set_state(n))
        row.addWidget(b)
    lay.addLayout(row)

    row2 = QHBoxLayout()
    bo, bc = QPushButton("opening"), QPushButton("closing")
    bo.clicked.connect(orb.play_opening)
    bc.clicked.connect(orb.play_closing)
    row2.addWidget(bo)
    row2.addWidget(bc)
    lay.addLayout(row2)

    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.valueChanged.connect(lambda v: orb.set_level(v / 100.0))
    auto = QCheckBox("fake speech level (ignores the slider)")
    lay.addWidget(QLabel("level"))
    lay.addWidget(slider)
    lay.addWidget(auto)

    clock = {"t": 0.0}

    def feed():
        clock["t"] += 0.033
        if auto.isChecked():
            orb.set_level(fake_speech_level(clock["t"]))
        else:
            orb.set_level(slider.value() / 100.0)   # keep the level fresh; stale values decay on purpose

    t = QTimer()
    t.setInterval(33)
    t.timeout.connect(feed)
    t.start()

    orb.set_state("listening")
    orb.play_opening()
    root.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
