"""Bake the Siri orb Lottie into separate raster layers (offline step; needs Chrome + lottie-web).

The Lottie is split into three independent groups of layers, each rendered on a transparent
background with lottie-web, so the runtime shader never needs a Lottie player:

  base   : dark disc + glass rim            (static)  -> base.png
  top    : two soft white highlights        (static)  -> top.png   (blended 'overlay' at runtime)
  waves  : the animated colour waves        (moving)  -> waves/NNNN.png  (one per sampled time)
  full   : everything together              (only for checking the result)

The baked pulse (Null scale 94-107%) is neutralised: at runtime the pulse comes from the live level.

usage:
  python bake_orb.py ORIGINAL.json OUT_DIR --lottie-web PATH/lottie.min.js [--size 512]
         [--wave-times 0,2,4,...]  [--full-times 0,120,300]  [--chrome /usr/bin/google-chrome]
"""
import argparse
import copy
import io
import json
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT_OP = 1040  # allow rendering past the original 948-frame end (waves keep moving until ~979)


def neutral_pulse(data):
    data["layers"][0]["ks"]["s"] = {"a": 0, "k": [100, 100, 100]}
    data["op"] = ROOT_OP


def pick(data, names_or_idx):
    """Keep the null parent (index 0) plus the listed root-layer indices, in original order."""
    keep = {0, *names_or_idx}
    out = copy.deepcopy(data)
    out["layers"] = [l for i, l in enumerate(data["layers"]) if i in keep]
    return out


def variants(original):
    d = copy.deepcopy(original)
    neutral_pulse(d)
    # index map of the original root layers: 0 null, 1-2 white highlights, 3-9 matte+waves, 10 glass/disc, 11 black solid
    return {
        "base": pick(d, [10]),
        "top": pick(d, [1, 2]),
        "waves": pick(d, [3, 4, 5, 6, 7, 8, 9]),
        "full": pick(d, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]),
    }


class Renderer:
    def __init__(self, play, lottie_js, chrome, size):
        self.size = size
        self.lottie_js = Path(lottie_js).resolve()
        self.browser = play.chromium.launch(executable_path=chrome, args=["--no-sandbox", "--allow-file-access-from-files"])

    def render(self, data, times):
        page = self.browser.new_page(viewport={"width": self.size, "height": self.size})
        html = f"""<!doctype html><html><body style="margin:0;background:transparent">
<div id="a" style="width:{self.size}px;height:{self.size}px"></div>
<script src="file://{self.lottie_js}"></script>
<script>
window.ready=false;
window.anim=lottie.loadAnimation({{container:document.getElementById('a'),renderer:'svg',loop:false,autoplay:false,
  rendererSettings:{{preserveAspectRatio:'xMidYMid meet'}},animationData:{json.dumps(data)}}});
anim.addEventListener('DOMLoaded',()=>{{window.ready=true}});
</script></body></html>"""
        tmp = self.lottie_js.parent / "_bake_page.html"
        tmp.write_text(html)
        page.goto(f"file://{tmp}")
        page.wait_for_function("window.ready === true", timeout=30000)
        out = []
        for t in times:
            page.evaluate(f"anim.goToAndStop({t}, true)")
            page.wait_for_timeout(60)
            png = page.screenshot(type="png", omit_background=True)
            out.append(Image.open(io.BytesIO(png)).convert("RGBA"))
        page.close()
        return out

    def close(self):
        self.browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("original")
    ap.add_argument("out_dir")
    ap.add_argument("--lottie-web", required=True)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--wave-times", default="")
    ap.add_argument("--full-times", default="")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--skip-static", action="store_true")
    a = ap.parse_args()

    out = Path(a.out_dir)
    (out / "waves").mkdir(parents=True, exist_ok=True)
    (out / "full").mkdir(parents=True, exist_ok=True)
    original = json.loads(Path(a.original).read_text())
    v = variants(original)

    with sync_playwright() as p:
        r = Renderer(p, a.lottie_web, a.chrome, a.size)
        if not a.skip_static:
            r.render(v["base"], [0])[0].save(out / "base.png")
            r.render(v["top"], [0])[0].save(out / "top.png")
            print("static layers done")
        if a.wave_times:
            times = [float(x) for x in a.wave_times.split(",")]
            for t, im in zip(times, r.render(v["waves"], times)):
                im.save(out / "waves" / f"{int(t):04d}.png")
            print(f"{len(times)} wave frames done")
        if a.full_times:
            times = [float(x) for x in a.full_times.split(",")]
            for t, im in zip(times, r.render(v["full"], times)):
                im.save(out / "full" / f"{int(t):04d}.png")
            print(f"{len(times)} full frames done")
        r.close()


if __name__ == "__main__":
    sys.exit(main())
