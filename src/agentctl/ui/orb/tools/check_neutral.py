"""Exactness check: shader at neutral settings vs the original Lottie render (pulse removed).

usage: python check_neutral.py ASSETS_DIR BAKE_DIR OUT_DIR
Compares at the times that exist in BAKE_DIR/full. Wave frame index = t / 2 (atlas is 30 fps).
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
try:
    from agentctl.ui.orb.motion import OrbMotion
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from motion import OrbMotion  # type: ignore # noqa: E402
from preview_webgl import render_frames  # noqa: E402


def main():
    assets, bake, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    times = sorted(int(p.stem) for p in (bake / "full").glob("*.png") if int(p.stem) >= 40)
    m = OrbMotion(assets / "orb_meta.json")
    ulist = []
    for t in times:
        u = m.uniforms()
        u.update({"u_amp": 1.0, "u_scale": 1.0, "u_opacity": 1.0, "u_tint_amt": 0.0, "u_glow": 0.0,
                  "u_shift": [0.0, 0.0], "u_fx": -1.0, "u_frame_a": float(t // 2), "u_frame_b": float(t // 2), "u_frame_mix": 0.0})
        ulist.append(u)
    size = 512
    files = render_frames(assets, ulist, str(out / "neutral"), size=size, bg="black")
    for t, f in zip(times, files):
        mine = np.asarray(Image.open(f).convert("RGB"), dtype=np.float32)
        ref_img = Image.open(bake / "full" / f"{t:04d}.png").convert("RGBA")
        ref = np.asarray(ref_img, dtype=np.float32)
        ref = ref[..., :3] * (ref[..., 3:4] / 255.0)
        d = np.abs(mine - ref)
        print(f"t={t:4d}: mean diff {d.mean():.3f}/255   p99 {np.percentile(d, 99):.2f}   max {d.max():.0f}")


if __name__ == "__main__":
    main()
