"""Headless tests for orb_motion.py (no Qt, no GPU).   python test_motion.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orb_assets"))
from orb_motion import DENIED_SECONDS, OrbMotion  # noqa: E402

META = Path(__file__).resolve().parent.parent / "orb_assets" / "orb_meta.json"
DT = 1 / 60


def run(m, seconds, level=None):
    for _ in range(int(seconds / DT)):
        if level is not None:
            m.set_level(level)
        m.update(DT)


def test_level_attack_release():
    m = OrbMotion(META)
    m.set_state("listening")
    run(m, 0.3, level=1.0)
    assert m.level > 0.95, m.level
    m.set_level(0.0)
    m.update(DT)
    after_one_frame = m.level
    assert after_one_frame > 0.9, "release must be slow"
    run(m, 1.5, level=0.0)
    assert m.level < 0.01


def test_stale_level_decays():
    m = OrbMotion(META)
    m.set_level(1.0)
    run(m, 0.2)                   # level keeps being "fresh" for 0.3 s
    assert m.level > 0.5
    run(m, 1.5)                   # no more messages: must fall back to zero
    assert m.level < 0.02, m.level


def test_amplitude_follows_level():
    quiet, loud = OrbMotion(META), OrbMotion(META)
    for m, lv in ((quiet, 0.0), (loud, 1.0)):
        m.set_state("speaking")
        run(m, 1.0, level=lv)
    assert loud.amp > quiet.amp + 0.8
    assert loud.speed > quiet.speed + 0.7


def test_idle_ignores_level():
    m = OrbMotion(META)
    run(m, 1.0, level=1.0)
    assert m.amp < 0.3


def test_wave_frames_wrap():
    m = OrbMotion(META)
    m.set_state("speaking")
    run(m, 1.0, level=1.0)
    for _ in range(3000):
        m.set_level(1.0)
        u = m.update(DT)
        assert 0 <= u["u_frame_a"] < m.n_frames and 0 <= u["u_frame_b"] < m.n_frames
        assert 0.0 <= u["u_frame_mix"] < 1.0


def test_opening_then_callback():
    m = OrbMotion(META, visible=False)
    seen = []
    m.on_opened = lambda: seen.append("opened")
    m.play_opening()
    u = m.update(DT)
    assert m.visible and u["u_fx"] >= 0 and u["u_scale"] < 0.6
    run(m, 2.0)
    assert seen == ["opened"]
    assert m.uniforms()["u_fx"] < 0 and abs(m.uniforms()["u_scale"] - 1.0) < 0.1


def test_closing_hides():
    m = OrbMotion(META)
    seen = []
    m.on_closed = lambda: seen.append("closed")
    m.play_closing()
    run(m, 1.0)
    assert seen == ["closed"] and not m.visible and m.uniforms()["u_opacity"] == 0.0
    m.play_opening()
    m.update(DT)
    assert m.visible


def test_denied_shakes_then_returns_to_idle():
    m = OrbMotion(META)
    m.set_state("listening")
    run(m, 0.5)
    m.set_state("denied")
    shifts = []
    for _ in range(int(DENIED_SECONDS / DT) + 5):
        shifts.append(abs(m.update(DT)["u_shift"][0]))
    assert max(shifts) > 5.0
    assert m.state == "idle"
    assert m.uniforms()["u_shift"][0] == 0.0


def test_unknown_state_rejected():
    m = OrbMotion(META)
    try:
        m.set_state("dancing")
    except ValueError:
        return
    raise AssertionError("should reject unknown states")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
