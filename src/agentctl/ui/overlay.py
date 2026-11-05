import os
import sys
from pathlib import Path
import threading
import time
from typing import Optional

if "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
if "QT_LOGGING_RULES" not in os.environ:
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland.textinput=false;qt.qpa.wayland*=false"

from PyQt6.QtCore import QLoggingCategory, QPoint, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
QLoggingCategory.setFilterRules("qt.qpa.wayland.textinput=false\nqt.qpa.wayland*=false")
from PyQt6.QtGui import QColor, QGuiApplication, QKeyEvent, QPainter, QPalette, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from agentctl.config import (
    ASSETS_DIR,
    AUDIO_LEVEL_CUTOFF,
    FULLSCREEN_OVERLAY,
    LEVEL_SEND_RATE_HZ,
    ORB_ASSETS_DIR,
    ORB_SIZE,
    SILENCE_CUTOFF_MS,
    USE_FAKE_LEVEL,
    harness as harness_config,
)

from agentctl.client.daemon import DaemonClient
from agentctl.client.ws import DaemonWsClient
from agentctl.logger import logger
from agentctl.stt.engine import SttEngine
from agentctl.stt.microphone import MicrophoneStream
from agentctl.stt.vad import SilenceAccumulator
from agentctl.tts import KokoroTtsEngine, TtsPlaybackWorker
from agentctl.ui.edge_glow import EdgeGlowManager
from agentctl.ui.orb import OrbWidget
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
        self.vad = SilenceAccumulator(silence_threshold_ms=SILENCE_CUTOFF_MS, calibration_frames=22)
        self.engine = SttEngine(model_size="base.en", cpu_threads=4)

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
            # Cut off sub-threshold ambient room tone / microphone hiss
            if intensity < AUDIO_LEVEL_CUTOFF:
                intensity = 0.0
            self.on_intensity(intensity)

            now = time.time()
            # Forward mic level to daemon at configured rate (default 50 Hz)
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
            # Triggers after 480ms of confirmed NEW speech frames (rapid live interim results).
            # Never triggers during silence, keeping CPU usage near 0% when quiet.
            if (
                self.vad.has_spoken
                and not self._is_transcribing
                and self._running
                and self.vad.should_trigger_partial(min_new_speech_ms=480)
            ):
                self._is_transcribing = True
                audio_snapshot = self.vad.get_speech_audio_bytes()
                dur_sec = len(audio_snapshot) / 32000.0
                logger.whisper(f">>> Partial pass triggered (audio_snapshot={dur_sec:.2f}s, delta_frames>=12)...")

                def _async_partial(audio_data):
                    t0 = time.time()
                    try:
                        text = self.engine.transcribe_bytes(audio_data)
                        elapsed = time.time() - t0
                        if text:
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
                thresh_ms = self.vad.silence_threshold_frames * self.vad.frame_duration_ms
                logger.vad(f"Silence threshold ({thresh_ms}ms) reached! Total speech: {self.vad.total_speech_frames * 20}ms.")
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

                # Re-arm VAD for next turn in multi-turn conversation loop
                self.vad.reset()
                was_spoken = False

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
    tts_chunk_signal = pyqtSignal(str, bool)
    exit_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.client = DaemonClient()

        # Window flags: Frameless, translucent, always-on-top
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        # Option B: Full-screen transparent overlay with click-through
        if FULLSCREEN_OVERLAY:
            flags |= Qt.WindowType.WindowTransparentForInput

        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
        pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
        self.setPalette(pal)
        self.setStyleSheet("background: transparent;")

        # Layout: transcript pill sits above the native orb, positioned at bottom-center
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 64)
        self.layout.setSpacing(16)
        if FULLSCREEN_OVERLAY:
            self.layout.addStretch(1)

        # 1. Transcript card
        self.transcript_card = TranscriptCard(self)
        self.layout.addWidget(self.transcript_card, alignment=Qt.AlignmentFlag.AlignHCenter)

        # 2. Native OrbWidget
        self.orb = OrbWidget(ORB_ASSETS_DIR, orb_size=ORB_SIZE, start_visible=True)
        self.edge_glow = EdgeGlowManager(ORB_ASSETS_DIR)
        self.orb.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.orb.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.orb.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.orb.setAutoFillBackground(False)
        self.orb.setStyleSheet("background: transparent; border: none; outline: none;")
        self.layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Connect signals
        self.partial_signal.connect(self._handle_partial_transcript)
        self.final_signal.connect(self._handle_final_transcript)
        self.intensity_signal.connect(self._handle_intensity)
        self.ws_state_signal.connect(self._handle_ws_state)
        self.ws_level_signal.connect(self._handle_ws_level)
        self.ws_opacity_signal.connect(self._handle_ws_opacity)
        self.tts_chunk_signal.connect(self._handle_tts_chunk)
        self.exit_signal.connect(self._handle_exit)
        self.orb.closed.connect(self._on_exit_finished)

        self.stt_worker: Optional[SttWorker] = None
        self._is_closing = False

        # Kokoro TTS engine and streaming audio player
        self.tts_engine = KokoroTtsEngine()
        self.tts_player = TtsPlaybackWorker(
            mic_stream=None,
            on_level=lambda lvl: self._handle_tts_level(lvl),
            on_finished=lambda: self.ws_client.send_playback_finished() if hasattr(self, "ws_client") else None,
        )
        self.tts_player.start()

        # WebSocket push client (replaces polling timer)
        self.ws_client = DaemonWsClient(
            on_state=self.ws_state_signal.emit,
            on_level=lambda v: self.ws_level_signal.emit(float(v)),
            on_tts_chunk=lambda sentence, is_final: self.tts_chunk_signal.emit(sentence, is_final),
            on_exit=lambda goodbye_msg: self.exit_signal.emit(goodbye_msg),
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
                self.setFixedSize(geo.width(), geo.height())
            else:
                w = max(360, self.orb.width() + 64)
                h = max(240, self.orb.height() + 80)
                self.setFixedSize(w, h)
                x = geo.x() + (geo.width() - w) // 2
                y = geo.y() + geo.height() - h - 48
                self.move(x, y)

            if hasattr(self, "border"):
                self.border.setGeometry(0, 0, geo.width(), geo.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        if hasattr(self, "border"):
            self.border.setGeometry(0, 0, w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.end()

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
            self.edge_glow.set_state(state_name)
            if state_name == "listening":
                self.transcript_card.set_transcript("Listening...")
                self.transcript_card.animate_in()

    def _handle_ws_level(self, level: float):
        """Sets smoothed reactive audio/activity level on the orb and border."""
        if not self._is_closing:
            self.orb.set_level(level)
            self.edge_glow.set_level(level)

    def _handle_ws_opacity(self, value: float):
        pass

    def activate_and_show(self):
        """Wakes up the overlay, starts listening, and triggers one-shot opening pop."""
        logger.info("UI", "Overlay activated. Playing opening animation...")
        self._position_on_screen()
        # On Wayland compositors (COSMIC cosmic-comp, GNOME Mutter), calling showFullScreen()
        # places the surface on the exclusive-fullscreen layer, which forces the compositor
        # to render its solid opaque grey backdrop (#27292a) instead of the desktop.
        # Calling self.show() with setFixedSize(geo.width(), geo.height()) covers the screen
        # while keeping the background 100% transparent to the desktop underneath.
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

        # One-shot pop-in animation on orb, edge border glow, and smooth text box entrance
        self.orb.play_opening()
        self.edge_glow.play_opening()
        self.transcript_card.animate_in()

        # Start STT background worker
        self.stt_worker = SttWorker(
            on_partial=lambda t: self.partial_signal.emit(t),
            on_final=lambda t: self.final_signal.emit(t),
            on_intensity=lambda i: self.intensity_signal.emit(i),
            ws_client=self.ws_client,
        )
        self.stt_worker.start()
        self.tts_player.mic = self.stt_worker.mic

    def _handle_partial_transcript(self, text: str):
        self.transcript_card.set_transcript(text)

    def _handle_intensity(self, intensity: float):
        # Local direct level fallback
        if USE_FAKE_LEVEL:
            return
        self.orb.set_level(intensity)
        self.edge_glow.set_level(intensity)

    def _handle_final_transcript(self, text: str):
        self.transcript_card.set_transcript(text)
        self.transcript_card.set_submitted()
        self.orb.set_state("thinking")
        self.edge_glow.set_state("thinking")

        # Send intent to daemon for harness processing
        try:
            self.client.send_intent(text)
        except Exception as e:
            logger.warn("UI", f"Send intent error: {e}")

    def _handle_tts_chunk(self, sentence: str, is_final: bool):
        """Receives a finished sentence from daemon LLM stream, synthesizes & enqueues for immediate playback."""
        if self._is_closing or not sentence.strip():
            return
        self.transcript_card.set_transcript(sentence)
        try:
            samples, sr = self.tts_engine.synthesize(sentence)
            self.tts_player.enqueue_sentence(samples, sr, is_final)
        except Exception as e:
            logger.error("TTS", f"Error synthesizing speech: {e}")

    def _handle_tts_level(self, level: float):
        """Live audio loudness meter update during speech playback."""
        if not self._is_closing:
            self.orb.set_level(level)
            self.edge_glow.set_level(level)

    def _handle_exit(self, goodbye_msg: str):
        """Exit phrase detected by daemon."""
        self.end_session(goodbye_msg)

    def end_session(self, goodbye_msg: Optional[str] = None):
        """Unified session exit: speaks goodbye, plays closing animation, and exits cleanly."""
        if self._is_closing:
            return
        logger.info("UI", "Session end: playing goodbye line then dismissing overlay...")
        self._is_closing = True

        msg = goodbye_msg or "Goodbye!"
        try:
            samples, sr = self.tts_engine.synthesize(msg)
            import sounddevice as sd
            sd.play(samples, sr)
            sd.wait()
        except Exception as e:
            logger.warn("TTS", f"Could not play goodbye audio: {e}")

        if self.stt_worker:
            self.stt_worker.stop()
        if hasattr(self, "tts_player") and self.tts_player:
            self.tts_player.stop()

        self.transcript_card.animate_out()
        self.orb.play_closing()
        self.edge_glow.play_closing()

        # Clean exit after closing animation finishes
        QTimer.singleShot(800, self._on_exit_finished)

    def cancel_and_exit(self):
        """Hotkey re-press or Esc cancellation path."""
        try:
            self.client.cancel()
        except Exception as e:
            logger.warn("UI", f"Cancel request error: {e}")

        self.end_session()

    def _on_exit_finished(self):
        """Clean teardown: stops timers, workers, and immediately releases all memory."""
        if self.stt_worker:
            self.stt_worker.stop()
        if hasattr(self, "ws_client") and self.ws_client:
            self.ws_client.stop()
        if hasattr(self, "edge_glow"):
            self.edge_glow.close()
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
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
    pal.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
    app.setPalette(pal)
    app.setStyleSheet("QWidget { background: transparent; }")
    window = AgentOverlayWindow()
    window.activate_and_show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
