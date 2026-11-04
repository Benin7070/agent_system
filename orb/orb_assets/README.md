# Voice orb: assets and integration notes

A native (no browser, no Lottie player) replacement for the old orb. It looks like the chosen Lottie
animation and reacts live to a level value (0..1) and to the assistant state.

## What is in this folder (runtime files)

| File | What it is |
|---|---|
| `orb_widget.py` | `OrbWidget`, a PyQt6 `QOpenGLWidget`. This is the only thing the app imports. |
| `orb_motion.py` | Pure-Python behaviour: states, level smoothing, opening/closing curves. No Qt, no GPU. |
| `orb.vert`, `orb.frag` | GLSL shaders (`#version 330 core` is prepended by the loader). |
| `orb_base.png` | Dark disc + glass rim (static). Premultiplied RGBA. |
| `orb_top.png` | Soft highlight mask, blended "overlay" in the shader. Premultiplied RGBA. |
| `orb_atlas.png` | All 474 wave frames (30 fps, seamless 15.8 s loop) in one 8000x840 grid. Premultiplied RGBA. |
| `orb_meta.json` | Atlas layout, wave crop box, baseline (all in Lottie comp units). |
| `orb_test.py` | Test window: state buttons, level slider, fake speech level. No microphone needed. |
| `orb_preview.gif` | How it should look: opening, listening, speaking, closing. |

NOTE for the integrating agent: the first plan listed a `wave_data.json` with bezier paths. That was replaced by
`orb_atlas.png` + `orb_meta.json` because pre-rendered wave frames reproduce the original exactly and cost far
less GPU than live blurring. Follow this README where it differs from the task prompt.

## Public API (`OrbWidget`)

```python
from orb_widget import OrbWidget
OrbWidget.configure_format()            # once, BEFORE QApplication is created
app = QApplication(sys.argv)
orb = OrbWidget("path/to/orb_assets", orb_size=240)   # orb diameter in logical pixels
orb.set_state("listening")   # idle | listening | thinking | speaking | confirm | denied
orb.set_level(0.4)           # 0..1; send the latest value ~30x per second
orb.play_opening()           # pop-in + ring + sparks, then the current state continues
orb.play_closing()           # shrink + fade, then `closed` signal; rendering stops
orb.set_orb_size(300)
orb.closed.connect(...)      # also: orb.opened
```

All methods are thread-safe (they go through Qt signals). `denied` is a one-shot (0.85 s red flash + shake) and
returns to `idle` by itself. The widget starts visible; call `play_opening()` to make it appear with the pop.

Behaviour rules built in:
- Level: fast attack (about 45 ms), slow release (about 200 ms). If no level arrives for 0.3 s it decays to 0,
  so a dropped connection or a missing message can never leave the orb stuck "loud".
- Rendering stops entirely when the orb is hidden after `play_closing()`; any call wakes it again.
- Wave height (vertical stretch around the centre line), wave speed and orb scale follow the level.
  `idle` ignores the level; `thinking` is slow and steady (feed it a token-rate level later);
  `speaking` is taller and faster; `confirm` is amber with a slow beat; `denied` is red with a shake.

## How it works (for debugging)

The Lottie was split offline into static layers (disc + rim, highlights) and the animated waves, each rendered
once. At runtime the shader stacks them: base, waves (two neighbouring atlas frames cross-faded, stretched
vertically by `u_amp`), then the highlights in "overlay" blend. Opening effects (bloom, two rings, 12 sparks) are
drawn procedurally in the same shader, driven by `u_fx`. Everything the shader needs per frame comes from
`OrbMotion.update(dt)` as a plain dict of uniforms.

## What was verified, and what was not

Verified here:
- With neutral settings the shader output matches the original Lottie render: mean difference about
  0.2 out of 255 (99th percentile 3, worst pixel 28, at thin edges), at 5 different times.
- The wave loop is seamless (jump at the loop point 0.026 vs 0.024 between normal neighbouring frames).
- Shader compiles and renders correctly as WebGL2 (same GLSL body, `#version 300 es` header) in headless Chrome;
  all states, opening, denied and closing were rendered and looked at.
- `orb_motion.py`: 9 unit tests pass (`orb_tools/test_motion.py`): level attack/release, stale-level decay,
  amplitude follows level, frame wrap-around, opening/closing callbacks, denied returns to idle.

NOT verified (no OpenGL context was available in the build sandbox):
- Running `orb_widget.py` itself under a real Qt OpenGL context. It compiles, and the PyQt6 names it uses were
  checked, but the first real run is `python orb_test.py` on your machine. If something fails there, send the
  traceback; the likely suspects are the GL 4.1 core context request and texture upload.
- CPU/GPU use and latency on your hardware. Expected to be light (one full-screen quad, 3 textures, 27 MB
  atlas), but measure it.
- Click-through / transparency behaviour on Wayland (Pop!_OS/COSMIC). `orb_test.py --transparent` is the quick check.

## Requirements
- PyQt6 with `QtOpenGL` and `QtOpenGLWidgets`; an OpenGL 4.1 core capable driver (GL 3.3 shaders inside).
- Keep the 8000x840 atlas below the GPU's texture size limit (16384 on any current GPU; 8192 is safe everywhere).

## Rebuilding or restyling (`orb_tools/`)
Only needed if the source Lottie changes. Needs Chrome and the `lottie-web` npm package, offline.
1. `bake_orb.py` renders base/top/waves from the Lottie (pulse removed).
2. `pack_orb.py` makes the atlas, seamless loop and `orb_meta.json`.
3. `verify_composite.py` and `check_neutral.py` re-check exactness; `preview_states.py` renders contact sheets.
