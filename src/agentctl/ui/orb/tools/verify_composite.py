"""Check that base + waves + top (overlay) reproduces the original full render.

usage: python verify_composite.py BAKE_DIR [out_sheet.png]
Prints the mean and max difference (0-255) over a black backdrop and the alpha difference per frame.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def load(p):
    a = np.asarray(Image.open(p).convert("RGBA"), dtype=np.float32) / 255.0
    rgb, al = a[..., :3], a[..., 3:4]
    return rgb, al  # straight alpha


def composite(base, wave, top_alpha):
    """Premultiplied math. Returns (straight rgb, alpha)."""
    cb, ab = base
    cw, aw = wave
    # waves: normal 'over' base
    a1 = aw + ab * (1 - aw)
    p1 = cw * aw + cb * ab * (1 - aw)
    c1 = np.where(a1 > 1e-6, p1 / np.maximum(a1, 1e-6), 0)
    # top: white, overlay blend onto (c1, a1)
    ov = np.where(c1 <= 0.5, 2 * c1, 1.0)
    a_s = top_alpha
    p = a_s * (1 - a1) * 1.0 + a_s * a1 * ov + (1 - a_s) * a1 * c1
    ao = a_s + a1 * (1 - a_s)
    return np.where(ao > 1e-6, p / np.maximum(ao, 1e-6), 0), ao


def over_black(rgb, a):
    return rgb * a


def main():
    d = Path(sys.argv[1])
    base = load(d / "base.png")
    top_a = load(d / "top.png")[1]
    rows = []
    for f in sorted((d / "waves").glob("*.png")):
        full_p = d / "full" / f.name
        if not full_p.exists():
            continue
        c, a = composite(base, load(f), top_a)
        fc, fa = load(full_p)
        mine, ref = over_black(c, a), over_black(fc, fa)
        diff = np.abs(mine - ref) * 255
        adiff = np.abs(a - fa) * 255
        print(f"{f.stem}: mean {diff.mean():.3f}  p99 {np.percentile(diff, 99):.2f}  max {diff.max():.1f}  | alpha mean {adiff.mean():.3f} max {adiff.max():.1f}")
        rows.append((ref, mine, diff))
    if len(sys.argv) > 2 and rows:
        s = 256
        sheet = Image.new("RGB", (s * 3, s * len(rows)))
        for i, (ref, mine, diff) in enumerate(rows):
            for j, im in enumerate((ref, mine, np.clip(diff * 8 / 255, 0, 1))):
                sheet.paste(Image.fromarray((im * 255).astype(np.uint8)).resize((s, s)), (j * s, i * s))
        sheet.save(sys.argv[2])
        print("sheet", sys.argv[2], "(columns: original | rebuilt | difference x8)")


if __name__ == "__main__":
    main()
