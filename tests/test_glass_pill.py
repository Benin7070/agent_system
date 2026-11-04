"""Tests for TranscriptCard: 9-slice paint, pill width pyqtProperty, animate_in/out sequencing."""
from PyQt6.QtWidgets import QApplication


def _app():
    return QApplication.instance() or QApplication([])


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


def test_vignette_asset_exists():
    """The vignette PNG asset must be loadable as a valid QPixmap."""
    from PyQt6.QtGui import QPixmap
    from agentctl.config import ASSETS_DIR
    app = _app()
    pix = QPixmap(str(ASSETS_DIR / "vignette_bottom.png"))
    assert not pix.isNull(), "vignette_bottom.png must load as a valid QPixmap"
    assert pix.height() == 400
