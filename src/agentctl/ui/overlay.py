import os
import sys
from pathlib import Path
import threading
import time
from typing import Optional

from PyQt6.QtCore import QPoint, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

# Add orb_assets to sys.path so orb_widget and orb_motion import cleanly
from agentctl.config import (
    ASSETS_DIR,
    FULLSCREEN_OVERLAY,
    LEVEL_SEND_RATE_HZ,
    ORB_ASSETS_DIR,
    ORB_SIZE,
    USE_FAKE_LEVEL,
)

if str(ORB_ASSETS_DIR) not in sys.path:
    sys.path.insert(0, str(ORB_ASSETS_DIR))

from orb_widget import OrbWidget

from agentctl.client.daemon import DaemonClient
from agentctl.client.ws import DaemonWsClient
from agentctl.logger import logger
from agentctl.stt.engine import SttEngine
from agentctl.stt.microphone import MicrophoneStream
from agentctl.stt.vad import SilenceAccumulator
from agentctl.ui.border_widget import BorderWidget
from agentctl.ui.screens import get_active_screen
from agentctl.ui.transcript import TranscriptCard


# --- Placeholder Level Generators & Integration Hooks (Item 4) ---

def emit_thinking_pulse(step: int) -> float:
    """Placeholder generator for thinking state: creates a gentle pulsating wave."""
    import math
    return float(max(0.0, min(1.0, 0.25 + 0.2 * math.sin(step * 0.25))))


def emit_speaking_level(step: int) -> float:
    """Placeholder generator for speaking state: simulates conversational speech dynamics."""
    import math
    return float(max(0.0, min(1.0, 0.35 + 0.35 * abs(math.sin(step * 0.35) * math.cos(step * 0.15)))))


def on_llm_token(token: str, token_index: int, ws_client: Optional[DaemonWsClient] = None):
    """Hook point: Called when a streamed LLM token arrives.
    Each streamed token emits a pulse to drive the thinking orb."""
    if ws_client:
        ws_client.send_level(0.65)


def on_tts_audio_chunk(pcm_chunk: bytes, ws_client: Optional[DaemonWsClient] = None):
    """Hook point: Called when TTS audio is played to the speaker.
    Calculates audio chunk RMS loudness and feeds it directly to the speaking orb."""
    if ws_client and pcm_chunk:
        rms = SttEngine.calculate_rms_intensity(pcm_chunk)
        ws_client.send_level(rms)


class SttWorker(threading.Thread):
    """Background audio listener reading mic frames and running non-blocking VAD/Whisper."""

    def __init__(self, on_partial, on_final, on_intensity, ws_client: Optional[DaemonWsClient] = None):
        super().__init__(daemon=True)
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_intensity = on_intensity
        self.ws_client = ws_client
        self._running = False
        self._is_transcribing = False
        self._partial_thread: Optional[threading.Thread] = None
        self.mic = MicrophoneStream()
        self.vad = SilenceAccumulator(silence_threshold_ms=1400, calibration_frames=22)
        self.engine = SttEngine(model_size="base.en", cpu_threads=2)

    def run(self):
        self._running = True
        try:
            self.mic.start()
            logger.info("AUDIO", "Microphone stream opened (16kHz, 16-bit mono).")
            logger.info("STT", f"Whisper engine ready (device={self.engine.device}, compute={self.engine.compute_type}, threads={self.engine.cpu_threads}).")
        except Exception as e:
            logger.warn("STT", f"Could not start microphone: {e}")
            return

        last_audio_log = time.time()
        last_level_push = time.time()
        level_interval = 1.0 / max(10, LEVEL_SEND_RATE_HZ)
        was_spoken = False

        while self._running:
            frame = self.mic.read_frame(timeout=0.02)
            if frame is None:
                continue

            intensity = SttEngine.calculate_rms_intensity(frame)
            self.on_intensity(intensity)

            now = time.time()
            # Forward mic level to daemon at ~30 Hz (item 4)
            if now - last_level_push >= level_interval:
                if self.ws_client:
                    self.ws_client.send_level(intensity)
                last_level_push = now

            was_calibrating = self.vad.calibration_count < self.vad.calibration_frames
            silence_triggered = self.vad.process_frame(frame)
            if was_calibrating and self.vad.calibration_count >= self.vad.calibration_frames:
                logger.info("VAD", f"Noise floor calibrated: speech threshold set to {self.vad.dynamic_speech_threshold:.4f} (filtering ambient fan/room hiss).")

            # Detect voice onset
            if not was_spoken and self.vad.has_spoken:
                was_spoken = True
                logger.vad(f"Speech onset detected! Initial frame energy={intensity:.4f}. Accumulating frames...")

            # Periodic live diagnostic log every 500ms
            if now - last_audio_log > 0.5:
                logger.audio(
                    rms=intensity,
                    is_speech=self.vad.last_frame_speech,
                    has_spoken=self.vad.has_spoken,
                    consec_silent=self.vad.consecutive_silent_frames,
                    total_speech=self.vad.total_speech_frames,
                )
                last_audio_log = now

            # Delta-Speech Gated partial transcription:
            # ONLY triggers if at least 650ms of NEW speech frames were added.
            # Never triggers during silence, keeping CPU usage near 0% when quiet.
            if (
                self.vad.has_spoken
                and not self._is_transcribing
                and self._running
                and self.vad.should_trigger_partial(min_new_speech_ms=650)
            ):
                self._is_transcribing = True
                audio_snapshot = self.vad.get_speech_audio_bytes()
                dur_sec = len(audio_snapshot) / 32000.0
                logger.whisper(f">>> Partial pass triggered (audio_snapshot={dur_sec:.2f}s, delta_frames>=32)...")

                def _async_partial(audio_data):
                    t0 = time.time()
                    try:
                        text = self.engine.transcribe_bytes(audio_data)
                        elapsed = time.time() - t0
                        if text and self._running:
                            logger.whisper(f"<<< Partial result in {elapsed:.2f}s: \"{text}\"")
                            self.on_partial(text)
                        else:
                            logger.whisper(f"<<< Partial result in {elapsed:.2f}s: (empty/noise)")
                    except Exception as e:
                        logger.warn("WHISPER", f"Partial transcription error: {e}")
                    finally:
                        self._is_transcribing = False

                self._partial_thread = threading.Thread(
                    target=_async_partial, args=(audio_snapshot,), daemon=True
                )
                self._partial_thread.start()

            if silence_triggered:
                logger.vad(f"Silence threshold (1400ms) reached! Total speech: {self.vad.total_speech_frames * 20}ms.")
                self._running = False
                self.mic.stop()
                if self._partial_thread and self._partial_thread.is_alive():
                    self._partial_thread.join(timeout=0.6)

                speech_bytes = self.vad.get_speech_audio_bytes()
                dur_sec = len(speech_bytes) / 32000.0
                logger.whisper(f">>> Final transcription pass on {dur_sec:.2f}s audio (trailing silence clipped)...")
                t0 = time.time()
                final = self.engine.transcribe_bytes(speech_bytes)
                elapsed = time.time() - t0

                if final:
                    logger.success("WHISPER", f"<<< Final result in {elapsed:.2f}s: \"{final}\" -> dispatching intent")
                    self.on_final(final)
                else:
                    logger.warn("WHISPER", f"<<< Final result in {elapsed:.2f}s was empty.")
                break

    def stop(self):
        self._running = False
        self.mic.stop()


class AgentOverlayWindow(QWidget):
    """Voice assistant overlay window featuring native OrbWidget and live push events."""

    partial_signal = pyqtSignal(str)
    final_signal = pyqtSignal(str)
    intensity_signal = pyqtSignal(float)
    ws_state_signal = pyqtSignal(str)
    ws_level_signal = pyqtSignal(float)
    ws_opacity_signal = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.client = DaemonClient()

        # Window flags: Frameless, translucent, always-on-top
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # Option B: Full-screen transparent overlay with click-through
        if FULLSCREEN_OVERLAY:
            flags |= Qt.WindowType.WindowTransparentForInput

        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        # ---- Bottom vignette (non-interactive, absolute-positioned) ----
        self.vignette = QLabel(self)
        self.vignette.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.vignette.setScaledContents(True)
        vig_pix = QPixmap(str(ASSETS_DIR / "vignette_bottom.png"))
        self.vignette.setPixmap(vig_pix)
        self.vignette.lower()

        # ---- Full-screen border glow (absolute-positioned sibling) ----
        self.border = BorderWidget(self)
        self.border.setGeometry(0, 0, 1920, 1080)
        self.border.lower()

        # Layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 48)
        self.layout.setSpacing(14)
        if FULLSCREEN_OVERLAY:
            self.layout.addStretch(1)

        # 1. Transcript card
        self.transcript_card = TranscriptCard(self)
        self.layout.addWidget(self.transcript_card, alignment=Qt.AlignmentFlag.AlignHCenter)

        # 2. Native OrbWidget
        self.orb = OrbWidget(ORB_ASSETS_DIR, orb_size=ORB_SIZE, start_visible=True)
        self.layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Connect signals
        self.partial_signal.connect(self._handle_partial_transcript)
        self.final_signal.connect(self._handle_final_transcript)
        self.intensity_signal.connect(self._handle_intensity)
        self.ws_state_signal.connect(self._handle_ws_state)
        self.ws_level_signal.connect(self._handle_ws_level)
        self.ws_opacity_signal.connect(self._handle_ws_opacity)
        self.orb.closed.connect(self._on_exit_finished)

        self.stt_worker: Optional[SttWorker] = None
        self._is_closing = False

        # WebSocket push client (Item 2 & 3: replaces 150ms polling timer)
        self.ws_client = DaemonWsClient(
            on_state=self.ws_state_signal.emit,
            on_level=self.ws_level_signal.emit,
        )
        self.ws_client.start()

        self._position_on_screen()

    def _position_on_screen(self):
        screen = get_active_screen()
        if screen:
            self.setScreen(screen)
            geo = screen.geometry()
            if FULLSCREEN_OVERLAY:
                self.setGeometry(geo)
            else:
                w = max(360, self.orb.width() + 64)
                h = max(240, self.orb.height() + 80)
                self.resize(w, h)
                x = geo.x() + (geo.width() - w) // 2
                y = geo.y() + geo.height() - h - 48
                self.move(x, y)

            if hasattr(self, "border"):
                self.border.setGeometry(0, 0, geo.width(), geo.height())

            VIG_H = 400
            if hasattr(self, "vignette"):
                self.vignette.setGeometry(0, geo.height() - VIG_H, geo.width(), VIG_H)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_and_exit()
        else:
            super().keyPressEvent(event)

    def _handle_ws_state(self, state_name: str):
        """Dispatches daemon state push events to the orb."""
        logger.info("FSM", f"Push state event received: {state_name}")
        if self._is_closing:
            return

        state_name = state_name.lower()
        if state_name in ("idle", "closing") and self._is_closing:
            return

        if state_name == "closing":
            self.cancel_and_exit()
        else:
            self.orb.set_state(state_name)
            if hasattr(self, "border"):
                self.border.set_state(state_name)

    def _handle_ws_level(self, level: float):
        """Sets smoothed reactive audio/activity level on the orb and border."""
        if not self._is_closing:
            self.orb.set_level(level)
            if hasattr(self, "border"):
                self.border.set_level(level)

    def _handle_ws_opacity(self, value: float):
        if hasattr(self, "border"):
            self.border.set_opacity(value)

    def activate_and_show(self):
        """Wakes up the overlay, starts listening, and triggers one-shot opening pop."""
        logger.info("UI", "Overlay activated. Playing opening animation...")
        self._position_on_screen()
        if FULLSCREEN_OVERLAY:
            self.showFullScreen()
        else:
            self.show()

        # On Wayland compositors, reinforce screen assignment via QWindow handle
        if self.windowHandle():
            screen = get_active_screen()
            if screen:
                self.windowHandle().setScreen(screen)

        # Notify daemon
        try:
            self.client.activate()
        except Exception as e:
            logger.warn("UI", f"Daemon activation error: {e}")

        # One-shot pop-in animation
        self.orb.play_opening()
        if hasattr(self, "border"):
            self.border.set_state("listening")
            self.border.set_opacity(1.0)
        self.transcript_card.set_transcript("")
        self.transcript_card.animate_in()

        # Start STT background worker
        self.stt_worker = SttWorker(
            on_partial=lambda t: self.partial_signal.emit(t),
            on_final=lambda t: self.final_signal.emit(t),
            on_intensity=lambda i: self.intensity_signal.emit(i),
            ws_client=self.ws_client,
        )
        self.stt_worker.start()

    def _handle_partial_transcript(self, text: str):
        self.transcript_card.set_transcript(text)

    def _handle_intensity(self, intensity: float):
        # Local direct level fallback
        if USE_FAKE_LEVEL:
            return
        self.orb.set_level(intensity)
        if hasattr(self, "border"):
            self.border.set_level(intensity)

    def _handle_final_transcript(self, text: str):
        self.transcript_card.set_transcript(text)
        self.transcript_card.set_submitted()
        self.orb.set_state("thinking")
        if hasattr(self, "border"):
            self.border.set_state("thinking")

        # Send intent to daemon
        try:
            self.client.send_intent(text)
        except Exception as e:
            logger.warn("UI", f"Send intent error: {e}")

        # Simulate completion flash then clean exit
        QTimer.singleShot(1400, self.finish_success_and_exit)

    def finish_success_and_exit(self):
        """Natural completion path: plays closing animation then exits."""
        if self._is_closing:
            return
        logger.success("UI", "Intent processed. Dismissing orb...")
        self._is_closing = True
        if self.stt_worker:
            self.stt_worker.stop()

        if hasattr(self, "border"):
            self.border.set_opacity(0.0)
        self.transcript_card.animate_out()
        self.orb.play_closing()

        # Safety timer in case compositor drops signal
        QTimer.singleShot(800, self._on_exit_finished)

    def cancel_and_exit(self):
        """Hotkey re-press or Esc cancellation path."""
        if self._is_closing:
            return
        logger.info("UI", "Overlay cancelled. Dismissing orb...")
        self._is_closing = True

        if self.stt_worker:
            self.stt_worker.stop()

        try:
            self.client.cancel()
        except Exception as e:
            logger.warn("UI", f"Cancel request error: {e}")

        if hasattr(self, "border"):
            self.border.set_opacity(0.0)
        self.transcript_card.set_discarded()
        self.transcript_card.animate_out()
        self.orb.play_closing()

        # Safety timer in case compositor drops signal
        QTimer.singleShot(800, self._on_exit_finished)

    def _on_exit_finished(self):
        """Clean teardown: stops timers, workers, and immediately releases all memory."""
        if self.stt_worker:
            self.stt_worker.stop()
        if hasattr(self, "ws_client") and self.ws_client:
            self.ws_client.stop()
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()
        os._exit(0)

    def closeEvent(self, event):
        self._on_exit_finished()
        event.accept()


def main():
    OrbWidget.configure_format()
    app = QApplication(sys.argv)
    window = AgentOverlayWindow()
    window.activate_and_show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
