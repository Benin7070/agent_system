"""Tests for TranscriptCard: 9-slice paint, pill width pyqtProperty, animate_in/out sequencing."""
from PyQt6.QtWidgets import QApplication


_APP = None

def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_pill_property_clamp():
    """pillWidth must clamp to [4, MAX_W]; pillOpacity to [0,1]."""
    from agentctl.ui.transcript import TranscriptCard
    app = _app()
    card = TranscriptCard()
    card.pillWidth = -10
    assert card._pill_w >= 4
    card.pillWidth = 9999
    assert card._pill_w <= TranscriptCard.MAX_W
    card.pillOpacity = 2.0
    assert card._pill_opacity <= 1.0
    card.pillOpacity = -1.0
    assert card._pill_opacity >= 0.0


def test_pill_animate_in_starts_hidden():
    """Before animate_in(), pill should be invisible (opacity 0)."""
    from agentctl.ui.transcript import TranscriptCard
    app = _app()
    card = TranscriptCard()
    assert card._pill_opacity == 0.0


def test_pill_set_transcript_updates_label():
    """set_transcript() must update the label text."""
    from agentctl.ui.transcript import TranscriptCard
    app = _app()
    card = TranscriptCard()
    card._pill_opacity = 1.0   # skip animation gate
    card.set_transcript("Hello, world")
    assert "Hello" in card.label.text()


def test_pill_live_streaming_updates():
    """Live streaming interim results must update text directly with full opacity."""
    from agentctl.ui.transcript import TranscriptCard
    app = _app()
    card = TranscriptCard()
    card._pill_opacity = 1.0

    # 1. Initial placeholder to first word
    card.set_transcript("Hello")
    assert card.label.text() == "Hello"

    # 2. Subsequent live streaming words
    card.set_transcript("Hello can you")
    assert card.label.text() == "Hello can you"
    assert card.label._opacity_fx.opacity() == 1.0

    # 3. Further streaming words
    card.set_transcript("Hello can you hear me?")
    assert card.label.text() == "Hello can you hear me?"
    assert card.label._opacity_fx.opacity() == 1.0


def test_vignette_asset_exists():
    """The vignette PNG asset must be loadable as a valid QPixmap."""
    from PyQt6.QtGui import QPixmap
    from agentctl.config import ASSETS_DIR
    app = _app()
    pix = QPixmap(str(ASSETS_DIR / "vignette_bottom.png"))
    assert not pix.isNull(), "vignette_bottom.png must load as a valid QPixmap"
    assert pix.height() == 400


def test_pill_entrance_animation_progresses():
    """animate_in() must configure initial animation state and progress to final targets."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from agentctl.ui.transcript import TranscriptCard, _PILL_CAP

    app = _app()
    card = TranscriptCard()
    assert card.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert card.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    assert not card.autoFillBackground()

    # Call animate_in
    card.animate_in()
    assert card.pillOpacity == 0.0
    assert card.yOffset == 18
    assert card.pillWidth == _PILL_CAP * 2

    # Advance time and process Qt animation events
    QTest.qWait(350)

    assert card.pillOpacity > 0.95
    assert card.yOffset == 0
    assert card.pillWidth == TranscriptCard.MAX_W
