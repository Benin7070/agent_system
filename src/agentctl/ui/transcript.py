from PyQt6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, Qt, pyqtProperty
from PyQt6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QVBoxLayout
from agentctl.ui.styles import COLOR_TEXT_DISCARDED, COLOR_TEXT_PRIMARY, COLOR_TEXT_SECONDARY, TRANSCRIPT_CARD_STYLE


def make_spring_curve(stiffness: float, damping: float) -> QEasingCurve:
    """Approximate underdamped spring easing using a cubic bezier curve."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    overshoot = max(0.0, (stiffness / (damping ** 2)) - 1.0) * 0.08
    curve.addCubicBezierSegment(
        QPointF(0.22, 1.0 + overshoot),
        QPointF(0.36, 1.0),
        QPointF(1.0, 1.0),
    )
    return curve


class TranscriptCard(QFrame):
    """Frosted glass transcript card showing live speech recognition."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("transcriptCard")
        self.setStyleSheet(TRANSCRIPT_CARD_STYLE)
        self.setFixedWidth(432)
        self.setMinimumHeight(48)
        self.setMaximumHeight(128)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)

        self.label = QLabel("Listening…", self)
        self.label.setObjectName("transcriptText")
        self.label.setWordWrap(True)
        self.label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-style: italic;")
        layout.addWidget(self.label)

        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity_effect)

        self.slide_anim = None
        self.fade_anim = None

    def set_transcript(self, text: str):
        if not text:
            self.label.setText("Listening…")
            self.label.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-style: italic;")
        else:
            self.label.setText(text)
            self.label.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-style: normal;")
            self.setAccessibleName(text)

    def set_discarded(self):
        """Fade text to 40% indicating intent was cancelled/discarded."""
        self.label.setStyleSheet(f"color: {COLOR_TEXT_DISCARDED};")

    def set_submitted(self):
        """Fade text to 60% indicating intent was sent to processing."""
        self.label.setStyleSheet("color: rgba(244, 244, 245, 0.60);")

    def animate_in(self):
        """Slides in from bottom with spring curve."""
        self.show()
        curve = make_spring_curve(stiffness=280.0, damping=24.0)

        # Fade opacity 0.0 -> 1.0
        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(320)
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.setEasingCurve(curve)

        # Slide Y offset
        start_pos = QPoint(self.x(), self.y() + 20)
        end_pos = QPoint(self.x(), self.y())
        self.slide_anim = QPropertyAnimation(self, b"pos")
        self.slide_anim.setDuration(320)
        self.slide_anim.setStartValue(start_pos)
        self.slide_anim.setEndValue(end_pos)
        self.slide_anim.setEasingCurve(curve)

        self.fade_anim.start()
        self.slide_anim.start()

    def animate_out(self, on_finished=None):
        """Slides down and fades out."""
        curve = make_spring_curve(stiffness=320.0, damping=28.0)

        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(240)
        self.fade_anim.setStartValue(self.opacity_effect.opacity())
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.setEasingCurve(curve)

        start_pos = self.pos()
        end_pos = QPoint(self.x(), self.y() + 16)
        self.slide_anim = QPropertyAnimation(self, b"pos")
        self.slide_anim.setDuration(240)
        self.slide_anim.setStartValue(start_pos)
        self.slide_anim.setEndValue(end_pos)
        self.slide_anim.setEasingCurve(curve)

        if on_finished:
            self.fade_anim.finished.connect(on_finished)

        self.fade_anim.start()
        self.slide_anim.start()

