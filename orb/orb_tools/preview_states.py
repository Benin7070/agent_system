"""Render contact sheets of the orb in every state, plus opening and closing, using the real shader.

usage: python preview_states.py ASSETS_DIR OUT_DIR
Writes opening.png, states.png, closing.png and orb_preview.gif (an opening -> listening -> speaking -> closing run).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "orb_assets"))
from orb_motion import OrbMotion  # noqa: E402
from preview_webgl import render_frames  # noqa: E402

SIZE = 360
DT = 1 / 60


def run(m, seconds, level_fn=None, capture_every=None):
    """Advance the motion; returns captured uniform dicts."""
    caps, n = [], int(seconds / DT)
    for i in range(n):
        if level_fn:
            m.set_level(level_fn(m.clock))
        u = m.update(DT)
        if capture_every and i % capture_every == 0:
            caps.append(u)
    return caps


def sheet(files, labels, cols, path, size=SIZE):
    rows = (len(files) + cols - 1) // cols
    W = Image.new("RGB", (cols * size, rows * size), (22, 22, 28))
    for i, (f, lab) in enumerate(zip(files, labels)):
        im = Image.open(f).convert("RGB").resize((size, size))
        ImageDraw.Draw(im).text((6, 4), lab, fill=(255, 235, 120))
        W.paste(im, ((i % cols) * size, (i // cols) * size))
    W.save(path)


def main():
    assets, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    meta = assets / "orb_meta.json"

    # opening: frames at 60 fps frame numbers
    m = OrbMotion(meta)
    m.set_state("listening")
    m.play_opening()
    want = [2, 6, 10, 14, 18, 22, 28, 36, 46, 70]
    caps = []
    for i in range(1, 72):
        u = m.update(DT)
        if i in want:
            caps.append(u)
    files = render_frames(assets, caps, str(out / "o"), size=SIZE, bg="dark")
    sheet(files, [f"open f{w}" for w in want], 5, out / "opening.png")

    # states at a quiet and a loud level
    caps, labels = [], []
    for st in ("idle", "listening", "thinking", "speaking", "confirm"):
        for lv, nm in ((0.05, "quiet"), (0.9, "loud")):
            m = OrbMotion(meta)
            m.set_state(st)
            m.clock = 5.0
            run(m, 1.5, level_fn=lambda t, lv=lv: lv)
            caps.append(m.uniforms())
            labels.append(f"{st} {nm}")
    files = render_frames(assets, caps, str(out / "s"), size=SIZE, bg="dark")
    sheet(files, labels, 4, out / "states.png")

    # denied: shake + red flash
    m = OrbMotion(meta)
    run(m, 1.0)
    m.set_state("denied")
    caps, labels = [], []
    for i in range(1, 50):
        u = m.update(DT)
        if i in (3, 6, 9, 14, 20, 30, 40, 48):
            caps.append(u)
            labels.append(f"denied +{i / 60:.2f}s")
    files = render_frames(assets, caps, str(out / "d"), size=SIZE, bg="dark")
    sheet(files, labels, 4, out / "denied.png")

    # closing
    m = OrbMotion(meta)
    m.set_state("idle")
    run(m, 0.5)
    m.play_closing()
    caps, labels = [], []
    want = [2, 6, 10, 14, 18, 22, 28, 33]
    for i in range(1, 40):
        u = m.update(DT)
        if i in want:
            caps.append(u)
            labels.append(f"close f{i}")
    files = render_frames(assets, caps, str(out / "c"), size=SIZE, bg="dark")
    sheet(files, labels, 4, out / "closing.png")

    # gif: opening -> listening (speech-like level) -> speaking -> closing, every 2nd frame
    m = OrbMotion(meta, visible=False)
    m.set_state("listening")
    m.play_opening()
    caps = []
    import math
    for i in range(0, 60 * 9):
        t = i * DT
        if t > 1.8:
            m.set_state("listening" if t < 5.0 else "speaking")
            lv = max(0.0, 0.5 + 0.45 * math.sin(t * 7.0) * math.sin(t * 2.3)) if t < 8.0 else 0.0
            m.set_level(lv)
        if abs(t - 8.0) < DT:
            m.set_state("idle")
        if abs(t - 8.1) < DT:
            m.play_closing()
        u = m.update(DT)
        if i % 2 == 0:
            caps.append(u)
    files = render_frames(assets, caps, str(out / "g"), size=300, bg="dark")
    ims = [Image.open(f).convert("RGB") for f in files]
    ims[0].save(out / "orb_preview.gif", save_all=True, append_images=ims[1:], duration=33, loop=0, optimize=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
