import os
import sys
import threading
import time
from typing import Optional
from PyQt6.QtCore import QPoint, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeyEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from agentctl.client.daemon import DaemonClient
from agentctl.logger import logger
from agentctl.stt.engine import SttEngine
from agentctl.stt.microphone import MicrophoneStream
from agentctl.stt.vad import SilenceAccumulator
from agentctl.ui.animator import GlowOrbWidget
from agentctl.ui.transcript import TranscriptCard, make_spring_curve


class SttWorker(threading.Thread):
    """Background audio listener reading mic frames and running non-blocking VAD/Whisper."""

    def __init__(self, on_partial, on_final, on_intensity):
        super().__init__(daemon=True)
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_intensity = on_intensity
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
        was_spoken = False

        while self._running:
            frame = self.mic.read_frame(timeout=0.02)
            if frame is None:
                continue

            intensity = SttEngine.calculate_rms_intensity(frame)
            self.on_intensity(intensity)

            was_calibrating = self.vad.calibration_count < self.vad.calibration_frames
            silence_triggered = self.vad.process_frame(frame)
            if was_calibrating and self.vad.calibration_count >= self.vad.calibration_frames:
                logger.info("VAD", f"Noise floor calibrated: speech threshold set to {self.vad.dynamic_speech_threshold:.4f} (filtering ambient fan/room hiss).")

            # Detect voice onset
            if not was_spoken and self.vad.has_spoken:
                was_spoken = True
                logger.vad(f"Speech onset detected! Initial frame energy={intensity:.4f}. Accumulating frames...")

            now = time.time()
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
    """Apple-style floating voice assistant overlay window."""

    partial_signal = pyqtSignal(str)
    final_signal = pyqtSignal(str)
    intensity_signal = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.client = DaemonClient()

        # Frameless, translucent, always on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        self.setFixedWidth(480)
        self.setMinimumHeight(160)

        # Layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 8, 24, 0)
        self.layout.setSpacing(8)

        # 1. Transcript card (slides down/up above orb)
        self.transcript_card = TranscriptCard(self)
        self.layout.addWidget(self.transcript_card, alignment=Qt.AlignmentFlag.AlignHCenter)

        # 2. Glowing Orb widget
        self.orb = GlowOrbWidget(self)
        self.layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Connect signals
        self.partial_signal.connect(self._handle_partial_transcript)
        self.final_signal.connect(self._handle_final_transcript)
        self.intensity_signal.connect(self._handle_intensity)

        self.stt_worker: Optional[SttWorker] = None
        self._is_closing = False

        # Status poller to react immediately if cancelled externally via global hotkey
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._check_daemon_status)
        self.poll_timer.start(150)

        self._position_on_screen()

    def _position_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.geometry()
            x = (screen_geo.width() - self.width()) // 2
            y = screen_geo.height() - self.height() - 48
            self.move(x, y)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_and_exit()
        else:
            super().keyPressEvent(event)

    def _check_daemon_status(self):
        if self._is_closing:
            return
        try:
            status = self.client.get_status()
            state = status.get("data", {}).get("state", "IDLE")
            if state in ("CANCELLING", "IDLE") and not self._is_closing:
                self.cancel_and_exit()
        except Exception:
            pass

    def activate_and_show(self):
        """Wakes up the overlay, starts listening, and slides in."""
        logger.info("UI", "Overlay activated & sliding in. Listening for user...")
        self._position_on_screen()
        self.show()

        # Notify daemon
        try:
            self.client.activate()
        except Exception as e:
            logger.warn("UI", f"Daemon activation error: {e}")

        self.orb.set_state(1)  # Listening
        self.transcript_card.set_transcript("")
        self.transcript_card.animate_in()

        # Smooth entry spring animation (shader alpha scale & position slide)
        self.orb.alphaScale = 0.0
        curve = make_spring_curve(stiffness=260.0, damping=22.0)
        self.anim_opacity = QPropertyAnimation(self.orb, b"alphaScale")
        self.anim_opacity.setDuration(380)
        self.anim_opacity.setStartValue(0.0)
        self.anim_opacity.setEndValue(1.0)
        self.anim_opacity.setEasingCurve(curve)

        start_pos = QPoint(self.x(), self.y() + 32)
        end_pos = QPoint(self.x(), self.y())
        self.anim_pos = QPropertyAnimation(self, b"pos")
        self.anim_pos.setDuration(380)
        self.anim_pos.setStartValue(start_pos)
        self.anim_pos.setEndValue(end_pos)
        self.anim_pos.setEasingCurve(curve)

        self.anim_opacity.start()
        self.anim_pos.start()

        # Start STT background worker
        self.stt_worker = SttWorker(
            on_partial=lambda t: self.partial_signal.emit(t),
            on_final=lambda t: self.final_signal.emit(t),
            on_intensity=lambda i: self.intensity_signal.emit(i),
        )
        self.stt_worker.start()

    def _handle_partial_transcript(self, text: str):
        self.transcript_card.set_transcript(text)

    def _handle_intensity(self, intensity: float):
        self.orb.set_intensity(intensity)

    def _handle_final_transcript(self, text: str):
        self.transcript_card.set_transcript(text)
        self.transcript_card.set_submitted()
        self.orb.set_state(2)  # Processing

        # Send intent to daemon
        try:
            self.client.send_intent(text)
        except Exception as e:
            print(f"[Overlay] Send intent error: {e}")

        # Simulate completion flash then clean exit
        QTimer.singleShot(1200, self.finish_success_and_exit)

    def finish_success_and_exit(self):
        """Natural completion path (Path B)."""
        if self._is_closing:
            return
        logger.success("UI", "Intent processed successfully. Exiting cleanly.")
        self._is_closing = True
        if self.stt_worker:
            self.stt_worker.stop()
        if self.poll_timer.isActive():
            self.poll_timer.stop()

        self.orb.set_state(3)  # Success

        self.transcript_card.animate_out()
        QTimer.singleShot(600, self._perform_exit_slide)

    def cancel_and_exit(self):
        """Hotkey re-press or Esc cancellation path (Path A)."""
        if self._is_closing:
            return
        logger.info("UI", "Overlay cancelled (Esc / hotkey toggle). Sliding out.")
        self._is_closing = True

        if self.stt_worker:
            self.stt_worker.stop()
        if self.poll_timer.isActive():
            self.poll_timer.stop()

        try:
            self.client.cancel()
        except Exception as e:
            print(f"[Overlay] Cancel request error: {e}")

        # Discarded state: text fades to 40%, card slides out
        self.transcript_card.set_discarded()
        self.transcript_card.animate_out()

        # Orb contracts smoothly with cool exit (state 5 = cancel, no red flash)
        self.orb.set_state(5)
        QTimer.singleShot(200, self._perform_exit_slide)

    def _perform_exit_slide(self):
        curve = make_spring_curve(stiffness=340.0, damping=30.0)
        self.exit_anim_opacity = QPropertyAnimation(self.orb, b"alphaScale")
        self.exit_anim_opacity.setDuration(280)
        self.exit_anim_opacity.setStartValue(self.orb.alphaScale)
        self.exit_anim_opacity.setEndValue(0.0)
        self.exit_anim_opacity.setEasingCurve(curve)

        start_pos = self.pos()
        end_pos = QPoint(self.x(), self.y() + 24)
        self.exit_anim_pos = QPropertyAnimation(self, b"pos")
        self.exit_anim_pos.setDuration(280)
        self.exit_anim_pos.setStartValue(start_pos)
        self.exit_anim_pos.setEndValue(end_pos)
        self.exit_anim_pos.setEasingCurve(curve)

        self.exit_anim_opacity.finished.connect(self._on_exit_finished)
        self.exit_anim_opacity.start()
        self.exit_anim_pos.start()

        # Hard fail-safe: guaranteed exit in 320ms even if compositor drops animation signal
        QTimer.singleShot(320, self._on_exit_finished)

    def _on_exit_finished(self):
        """Clean teardown: stops timers, workers, and immediately releases all memory."""
        if hasattr(self, "poll_timer") and self.poll_timer.isActive():
            self.poll_timer.stop()
        if self.stt_worker:
            self.stt_worker.stop()
        self.orb.stop_timer()
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()
        # Hard exit ensures all C extensions, Whisper weights, and OpenGL context are freed
        os._exit(0)

    def closeEvent(self, event):
        self._on_exit_finished()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = AgentOverlayWindow()
    window.activate_and_show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
