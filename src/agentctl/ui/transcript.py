"""Liquid-glass transcript pill widget.

Visual layers (bottom → top in paintEvent):
  1. glass_pill_bg.png  — 9-slice stretched to widget rect
  2. glass_pill_specular.png — centred in upper third, Screen blend (boost whites)
  3. QLabel with white text (child widget, positioned inside pill padding)

Animations:
  animate_in()    : pill expands from centre point → full width (centre-to-two-ends),
                    slight scale overshoot pop, then text fades bottom-to-top.
  animate_out()   : text fades, then pill collapses from two ends → centre point.
  set_transcript(): each call fades new text in with a bottom-to-top slide.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve, QPoint, QPointF, QPropertyAnimation,
    QRect, QRectF, QSequentialAnimationGroup, QSize,
    Qt, QTimer, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QImage, QPainter, QPainterPath,
    QPixmap, QTransform,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

from agentctl.config import ASSETS_DIR

# Loaded once at module level (shared across instances)
_PILL_BG: Optional[QPixmap]   = None
_PILL_SPEC: Optional[QPixmap] = None


def _load_assets():
    global _PILL_BG, _PILL_SPEC
    if _PILL_BG is None:
        bg_path = ASSETS_DIR / "glass_pill_bg.png"
        spec_path = ASSETS_DIR / "glass_pill_specular.png"
        if bg_path.exists():
            _PILL_BG = QPixmap(str(bg_path))
        if spec_path.exists():
            _PILL_SPEC = QPixmap(str(spec_path))


# 9-slice parameters for glass_pill_bg.png (480×72)
_PILL_NATIVE_W  = 480
_PILL_NATIVE_H  = 72
_PILL_CAP       = 36        # left and right cap widths (= RADIUS)
_PILL_SPEC_H    = 22        # height of specular highlight strip


def _draw_9slice(painter: QPainter, pixmap: QPixmap, rect: QRect, cap: int):
    """Draw pixmap using 9-slice scaling with given cap (corner) size."""
    sw, sh = pixmap.width(), pixmap.height()
    dw, dh = rect.width(), rect.height()
    x0, y0 = rect.x(), rect.y()

    if dw < cap * 2:
        painter.drawPixmap(rect, pixmap)
        return

    # Source rects (left-cap, centre, right-cap) × full height
    src_L = QRect(0,          0, cap, sh)
    src_C = QRect(cap,        0, sw - 2 * cap, sh)
    src_R = QRect(sw - cap,   0, cap, sh)

    # Destination rects
    dst_L = QRect(x0,              y0, cap, dh)
    dst_C = QRect(x0 + cap,        y0, dw - 2 * cap, dh)
    dst_R = QRect(x0 + dw - cap,   y0, cap, dh)

    painter.drawPixmap(dst_L, pixmap, src_L)
    painter.drawPixmap(dst_C, pixmap, src_C)
    painter.drawPixmap(dst_R, pixmap, src_R)


class _TextLabel(QLabel):
    """Inner text label with per-word bottom-to-top fade animation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        font = QFont("Inter", 15)
        font.setFamilies(["Inter", "SF Pro Text", "system-ui", "sans-serif"])
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)
        self.setStyleSheet("color: rgba(244,244,245,0.9); background: transparent;")

        self._opacity_fx = QGraphicsOpacityEffect(self)
        self._opacity_fx.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_fx)
        self._fade_anim: Optional[QPropertyAnimation] = None
        self._slide_anim: Optional[QPropertyAnimation] = None
        self._base_geo: Optional[QRect] = None

    def set_base_geometry(self, geo: QRect):
        self._base_geo = geo
        self.setGeometry(geo)

    def fade_in_text(self):
        """Bottom-to-top fade-in: Y shifts up 10 px while opacity 0→1."""
        for a in (self._fade_anim, self._slide_anim):
            if a and a.state() == QPropertyAnimation.State.Running:
                a.stop()

        curve = QEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_anim = QPropertyAnimation(self._opacity_fx, b"opacity")
        self._fade_anim.setDuration(240)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(curve)

        cur = self._base_geo if self._base_geo is not None else self.geometry()
        start = QRect(cur.x(), cur.y() + 10, cur.width(), cur.height())
        self._slide_anim = QPropertyAnimation(self, b"geometry")
        self._slide_anim.setDuration(240)
        self._slide_anim.setStartValue(start)
        self._slide_anim.setEndValue(cur)
        self._slide_anim.setEasingCurve(curve)

        self._opacity_fx.setOpacity(0.0)
        self._fade_anim.start()
        self._slide_anim.start()

    def fade_out_text(self):
        curve = QEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim = QPropertyAnimation(self._opacity_fx, b"opacity")
        self._fade_anim.setDuration(160)
        self._fade_anim.setStartValue(self._opacity_fx.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(curve)
        self._fade_anim.start()


class TranscriptCard(QWidget):
    """Liquid-glass pill transcript card with centre-expand opening and collapse closing."""

    MIN_W   = 220
    MAX_W   = 540
    PILL_H  = 56   # fixed pill height in logical px

    def __init__(self, parent=None):
        super().__init__(parent)
        _load_assets()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

        self._pill_w: int = self.MAX_W    # current rendered pill width
        self._pill_opacity: float = 0.0  # 0..1; controls whole card
        self._y_offset: int = 0          # vertical entrance animation offset

        # Fixed size for layout purposes; extra vertical room for entrance slide
        self.setFixedSize(self.MAX_W, self.PILL_H + 24)

        self.label = _TextLabel(self)
        self._update_label_geo()
        self.label.setText("Listening…")
        self.label.setStyleSheet("color: rgba(244,244,245,0.55); background: transparent; font-style: italic;")

        # Animation state
        self._pill_w_anim:    Optional[QPropertyAnimation] = None
        self._pill_op_anim:   Optional[QPropertyAnimation] = None
        self._y_anim:         Optional[QPropertyAnimation] = None

    def _update_label_geo(self):
        pad_h, pad_v = _PILL_CAP + 4, 8
        label_geo = QRect(
            pad_h, self._y_offset + pad_v,
            self.MAX_W - 2 * pad_h,
            self.PILL_H - 2 * pad_v,
        )
        self.label.set_base_geometry(label_geo)

    # ------------------------------------------------------------------ pyqtProperties for animation
    @pyqtProperty(int)
    def pillWidth(self) -> int:
        return self._pill_w

    @pillWidth.setter
    def pillWidth(self, v: int):
        self._pill_w = max(4, min(self.MAX_W, int(v)))
        self.update()

    @pyqtProperty(float)
    def pillOpacity(self) -> float:
        return self._pill_opacity

    @pillOpacity.setter
    def pillOpacity(self, v: float):
        self._pill_opacity = max(0.0, min(1.0, float(v)))
        self.update()

    @pyqtProperty(int)
    def yOffset(self) -> int:
        return self._y_offset

    @yOffset.setter
    def yOffset(self, v: int):
        self._y_offset = int(v)
        self._update_label_geo()
        self.update()

    # ------------------------------------------------------------------ public API
    def set_transcript(self, text: str):
        """Update text and trigger bottom-to-top fade-in of the label."""
        self.label.fade_out_text()
        if not text:
            self.label.setText("Listening…")
            self.label.setStyleSheet(
                "color: rgba(244,244,245,0.55); background: transparent; font-style: italic;")
        else:
            self.label.setText(text)
            self.label.setStyleSheet(
                "color: rgba(244,244,245,0.92); background: transparent; font-style: normal;")
            self.setAccessibleName(text)
        # Delay text fade-in depending on pill open state
        if self._pill_opacity > 0.3:
            QTimer.singleShot(80, self.label.fade_in_text)
        else:
            QTimer.singleShot(180, self.label.fade_in_text)

    def set_discarded(self):
        self.label.setStyleSheet("color: rgba(244,244,245,0.40); background: transparent;")

    def set_submitted(self):
        self.label.setStyleSheet("color: rgba(244,244,245,0.60); background: transparent;")

    def animate_in(self):
        """Fluid entrance animation: slides up, expands from center, and fades in."""
        self._pill_w = _PILL_CAP * 2
        self._pill_opacity = 0.0
        self._y_offset = 18
        self._update_label_geo()
        self.show()

        # Stop any in-progress animations
        for a in (self._pill_w_anim, self._pill_op_anim, self._y_anim):
            if a and a.state() == QPropertyAnimation.State.Running:
                a.stop()

        # Phase 1: fade pill opacity 0.0 -> 1.0 (280ms)
        self._pill_op_anim = QPropertyAnimation(self, b"pillOpacity")
        self._pill_op_anim.setDuration(280)
        self._pill_op_anim.setStartValue(0.0)
        self._pill_op_anim.setEndValue(1.0)
        self._pill_op_anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._pill_op_anim.start()

        # Phase 2: slide upward into position (18px -> 0px over 320ms)
        self._y_anim = QPropertyAnimation(self, b"yOffset")
        self._y_anim.setDuration(320)
        self._y_anim.setStartValue(18)
        self._y_anim.setEndValue(0)
        ease_y = QEasingCurve(QEasingCurve.Type.OutBack)
        ease_y.setOvershoot(1.10)
        self._y_anim.setEasingCurve(ease_y)
        self._y_anim.start()

        # Phase 3: expand pill width from center (280ms)
        self._pill_w_anim = QPropertyAnimation(self, b"pillWidth")
        self._pill_w_anim.setDuration(280)
        self._pill_w_anim.setStartValue(_PILL_CAP * 2)
        self._pill_w_anim.setEndValue(self.MAX_W)
        self._pill_w_anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        self._pill_w_anim.start()

        # Phase 4: text fade-in synchronized as pill settles
        QTimer.singleShot(140, self.label.fade_in_text)

    def animate_out(self, on_finished=None):
        """Text fades, then pill collapses two ends → centre, then fades."""
        self.label.fade_out_text()

        collapse_curve = QEasingCurve(QEasingCurve.Type.InBack)
        collapse_curve.setOvershoot(0.9)

        self._pill_w_anim = QPropertyAnimation(self, b"pillWidth")
        self._pill_w_anim.setDuration(220)
        self._pill_w_anim.setStartValue(self._pill_w)
        self._pill_w_anim.setEndValue(_PILL_CAP * 2)
        self._pill_w_anim.setEasingCurve(collapse_curve)

        self._pill_op_anim = QPropertyAnimation(self, b"pillOpacity")
        self._pill_op_anim.setDuration(200)
        self._pill_op_anim.setStartValue(self._pill_opacity)
        self._pill_op_anim.setEndValue(0.0)
        self._pill_op_anim.setEasingCurve(QEasingCurve(QEasingCurve.Type.InCubic))

        if on_finished:
            self._pill_op_anim.finished.connect(on_finished)

        QTimer.singleShot(80, self._pill_w_anim.start)
        QTimer.singleShot(100, self._pill_op_anim.start)

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event):
        if self._pill_opacity <= 0.001 or _PILL_BG is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self._pill_opacity)

        # Pill is always centred horizontally inside the fixed-width widget
        x_offset = (self.width() - self._pill_w) // 2
        y_offset = self._y_offset
        pill_rect = QRect(x_offset, y_offset, self._pill_w, self.PILL_H)

        # Layer 1: pill body (9-slice or scaled)
        _draw_9slice(painter, _PILL_BG, pill_rect, _PILL_CAP)

        # Layer 2: specular highlight (Screen blend)
        if _PILL_SPEC is not None and self._pill_w > _PILL_CAP * 2:
            spec_w = min(_PILL_SPEC.width(), self._pill_w - _PILL_CAP)
            spec_x = x_offset + (self._pill_w - spec_w) // 2
            spec_rect = QRect(spec_x, y_offset + 4, spec_w, _PILL_SPEC_H)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            painter.drawPixmap(spec_rect, _PILL_SPEC,
                               QRect(0, 0, _PILL_SPEC.width(), _PILL_SPEC.height()))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        painter.end()
