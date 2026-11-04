"""Render the real orb shader in headless Chrome (WebGL2) to check it without a Qt/OpenGL setup.

Used for verification only. It runs the SAME orb.vert / orb.frag and the SAME orb_motion.py logic as the app;
only the OpenGL host differs.

as a module:  render_frames(assets_dir, list_of_uniform_dicts, out_prefix, size=540, bg="dark")
"""
import base64
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

HEADER = "#version 300 es\nprecision highp float;\nprecision highp sampler2D;\n"

PAGE = """<!doctype html><html><body style="margin:0;background:__BG__">
<canvas id="c" width="__S__" height="__S__" style="width:__S__px;height:__S__px;display:block"></canvas>
<script>
const SRC = __SRC__;
const gl = document.getElementById('c').getContext('webgl2', {premultipliedAlpha: true, alpha: true});
function sh(type, src) { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; }
const prog = gl.createProgram();
gl.attachShader(prog, sh(gl.VERTEX_SHADER, SRC.vert)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, SRC.frag));
gl.linkProgram(prog);
if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
gl.useProgram(prog);
const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
const loc = gl.getAttribLocation(prog, 'a_pos'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
async function tex(unit, name, w, h) {
  const bytes = new Uint8Array(await (await fetch('/' + name + '.raw')).arrayBuffer());
  const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, bytes);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.uniform1i(gl.getUniformLocation(prog, name), unit);
}
window.ready = false;
(async () => {
  const D = __DIMS__;
  await tex(0, 'u_base', D.base[0], D.base[1]); await tex(1, 'u_top', D.top[0], D.top[1]); await tex(2, 'u_atlas', D.atlas[0], D.atlas[1]);
  window.ready = true;
})();
window.draw = (u) => {
  for (const [k, v] of Object.entries(u)) {
    const l = gl.getUniformLocation(prog, k); if (l === null) continue;
    if (typeof v === 'number') gl.uniform1f(l, v);
    else if (v.length === 2) gl.uniform2fv(l, v); else if (v.length === 3) gl.uniform3fv(l, v); else gl.uniform4fv(l, v);
  }
  gl.viewport(0, 0, __S__, __S__); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); gl.finish();
};
</script></body></html>"""


def render_frames(assets_dir, uniform_list, out_prefix, size=540, bg="dark", chrome="/usr/bin/google-chrome"):
    assets = Path(assets_dir)
    raws, dims = {}, {}
    for key, fname in (("u_base", "orb_base.png"), ("u_top", "orb_top.png"), ("u_atlas", "orb_atlas.png")):
        im = Image.open(assets / fname).convert("RGBA")
        raws[key] = np.asarray(im, dtype=np.uint8).tobytes()
        dims[key.replace("u_", "")] = list(im.size)
    src = {"vert": HEADER + (assets / "orb.vert").read_text(), "frag": HEADER + (assets / "orb.frag").read_text()}
    bgcss = {"dark": "#16161c", "black": "#000", "none": "transparent", "light": "#e8e8ee"}[bg]
    html = (PAGE.replace("__BG__", bgcss).replace("__S__", str(size))
            .replace("__SRC__", json.dumps(src)).replace("__DIMS__", json.dumps(dims)))

    outs = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=chrome, args=["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_page(viewport={"width": size, "height": size})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type in ("error",) else None)

        def route(r):
            url = r.request.url
            name = url.rsplit("/", 1)[-1]
            if name == "":
                r.fulfill(body=html, content_type="text/html")
            elif name.endswith(".raw"):
                r.fulfill(body=raws[name[:-4]], content_type="application/octet-stream")
            else:
                r.fulfill(status=404, body="")
        pg.route("http://orb.test/**", route)
        pg.goto("http://orb.test/")
        pg.wait_for_function("window.ready === true", timeout=60000)
        for i, u in enumerate(uniform_list):
            pg.evaluate("u => window.draw(u)", u)
            path = f"{out_prefix}_{i:03d}.png"
            pg.screenshot(path=path, omit_background=(bg == "none"))
            outs.append(path)
        if errs:
            print("PAGE ERRORS:", errs[:5], file=sys.stderr)
        b.close()
    return outs
