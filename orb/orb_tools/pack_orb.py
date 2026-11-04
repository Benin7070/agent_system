"""Pack baked orb layers into runtime assets.

usage: python pack_orb.py BAKE_DIR OUT_DIR [--scale 0.5] [--loop-frames 474] [--blend 15]

Reads   BAKE_DIR/base.png, top.png, waves/NNNN.png  (wave times t=0,2,4,... in 60 fps comp frames)
Writes  OUT_DIR/orb_base.png   dark disc + glass rim, premultiplied RGBA
        OUT_DIR/orb_top.png    soft highlight mask (alpha only matters), premultiplied RGBA
        OUT_DIR/orb_atlas.png  all wave frames in one grid, premultiplied RGBA
        OUT_DIR/orb_meta.json  sizes, grid layout, crop box and baseline in comp units

All PNGs hold PREMULTIPLIED colour. Load them as raw bytes and upload them as they are.
The wave loop is made seamless by cross-fading its end into frames the animation keeps playing after the
original loop point.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

COMP = 1080.0


def premult(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0
    a[..., :3] *= a[..., 3:4]
    return a


def to_u8(a: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8)


def smooth(x):
    return x * x * (3 - 2 * x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bake_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--scale", type=float, default=0.5, help="wave atlas scale relative to the 512px bake")
    ap.add_argument("--loop-frames", type=int, default=474, help="frames in the loop at 30 fps (948 comp frames / 2)")
    ap.add_argument("--blend", type=int, default=15, help="cross-fade frames used to hide the loop seam")
    ap.add_argument("--max-atlas", type=int, default=8192)
    a = ap.parse_args()

    bake, out = Path(a.bake_dir), Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    N, X = a.loop_frames, a.blend
    files = [bake / "waves" / f"{2 * j:04d}.png" for j in range(N + X)]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"missing wave frames, e.g. {missing[0]}")

    base_img = Image.open(bake / "base.png").convert("RGBA")
    size = base_img.size[0]
    k = COMP / size  # comp units per baked pixel

    # pass 1: union bounding box of the visible wave pixels
    x0 = y0 = 10**9
    x1 = y1 = -1
    for f in files:
        al = np.asarray(Image.open(f).convert("RGBA"))[..., 3]
        ys, xs = np.nonzero(al > 3)
        if len(xs):
            x0, x1 = min(x0, xs.min()), max(x1, xs.max())
            y0, y1 = min(y0, ys.min()), max(y1, ys.max())
    pad = 4
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(size, x1 + pad + 1), min(size, y1 + pad + 1)
    w, h = x1 - x0, y1 - y0
    print(f"wave bounding box (baked px): x {x0}-{x1}  y {y0}-{y1}  -> {w}x{h}")

    # pass 2: load cropped premultiplied frames
    frames = np.zeros((N + X, h, w, 4), dtype=np.float32)
    cy_sum, cy_w = 0.0, 0.0
    for j, f in enumerate(files):
        p = premult(Image.open(f))[y0:y1, x0:x1]
        frames[j] = p
        lum = p[..., :3].sum(axis=2) * p[..., 3]
        ys = np.arange(y0, y1, dtype=np.float32)[:, None]
        cy_sum += float((lum * ys).sum())
        cy_w += float(lum.sum())
    baseline_px = cy_sum / max(cy_w, 1e-6)

    # seamless loop: out[j] = mix(F[N+j], F[j], w) for the first X frames
    loop = frames[:N].copy()
    for j in range(X):
        wgt = smooth(j / X)
        loop[j] = frames[N + j] * (1 - wgt) + frames[j] * wgt
    step = np.abs(np.diff(loop, axis=0)).mean(axis=(1, 2, 3))
    seam = np.abs(loop[0] - loop[-1]).mean()
    print(f"loop seam diff {seam:.5f}  vs typical consecutive-frame diff {step.mean():.5f} (max {step.max():.5f})")
    raw_seam = np.abs(frames[0] - frames[N - 1]).mean()
    print(f"(without cross-fade the seam diff would be {raw_seam:.5f})")

    # downscale into the atlas
    s = a.scale
    cw, ch = int(np.ceil(w * s)), int(np.ceil(h * s))
    cols = max(1, a.max_atlas // cw)
    rows = int(np.ceil(N / cols))
    if rows * ch > a.max_atlas:
        raise SystemExit(f"atlas {cols * cw}x{rows * ch} exceeds {a.max_atlas}; lower --scale")
    atlas = np.zeros((rows * ch, cols * cw, 4), dtype=np.uint8)
    err = []
    for j in range(N):
        im = Image.frombytes("RGBa", (w, h), to_u8(loop[j]).tobytes())
        small = im.resize((cw, ch), Image.LANCZOS if s < 1 else Image.BILINEAR)
        arr = np.frombuffer(small.tobytes(), dtype=np.uint8).reshape(ch, cw, 4)
        r, c = divmod(j, cols)
        atlas[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw] = arr
        if j % 60 == 0:  # quality check: upscale back and compare
            back = small.resize((w, h), Image.BILINEAR)
            ref = to_u8(loop[j]).astype(np.float32)
            err.append(np.abs(np.frombuffer(back.tobytes(), dtype=np.uint8).reshape(h, w, 4).astype(np.float32) - ref).mean())
    print(f"atlas {cols * cw}x{rows * ch}  ({cols} cols x {rows} rows, cell {cw}x{ch})  "
          f"~{atlas.nbytes / 1e6:.0f} MB on the GPU;  scale {s}: mean error {np.mean(err):.3f}/255")
    Image.fromarray(atlas, "RGBA").save(out / "orb_atlas.png", optimize=True)

    # static layers
    bp = premult(base_img)
    Image.fromarray(to_u8(bp), "RGBA").save(out / "orb_base.png", optimize=True)
    tp = premult(Image.open(bake / "top.png"))
    Image.fromarray(to_u8(tp), "RGBA").save(out / "orb_top.png", optimize=True)

    meta = {
        "comp_size": COMP,
        "bake_size": size,
        "fps": 30,
        "frames": N,
        "loop_seconds": N / 30.0,
        "atlas": {"width": cols * cw, "height": rows * ch, "cols": cols, "rows": rows, "cell_w": cw, "cell_h": ch},
        # wave crop box in comp units (top-left origin, y down)
        "crop": {"x": x0 * k, "y": y0 * k, "w": w * k, "h": h * k},
        "baseline_y": float(baseline_px * k),
        "disc_radius": 390.0,
    }
    (out / "orb_meta.json").write_text(json.dumps(meta, indent=2))
    print("baseline y (comp units):", round(meta["baseline_y"], 1))
    print("wrote", out)


if __name__ == "__main__":
    main()
