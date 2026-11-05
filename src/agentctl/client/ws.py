"""WebSocket client for agentctl-daemon over Unix Domain Socket."""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional
import websockets
from agentctl.config import SOCKET_PATH
from agentctl.logger import logger


class DaemonWsClient(threading.Thread):
    """Background thread running an asyncio loop to stream events from agentctl-daemon."""

    def __init__(
        self,
        on_state: Callable[[str], None],
        on_level: Callable[[float], None],
        on_tts_chunk: Optional[Callable[[str, bool], None]] = None,
        on_exit: Optional[Callable[[str], None]] = None,
        socket_path: Optional[Path] = None,
    ):
        super().__init__(daemon=True)
        self.on_state = on_state
        self.on_level = on_level
        self.on_tts_chunk = on_tts_chunk
        self.on_exit = on_exit
        self.socket_path = socket_path or SOCKET_PATH
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws: Optional[websockets.ClientConnection] = None
        self._outgoing_queue: Optional[asyncio.Queue] = None

    def run(self):
        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._outgoing_queue = asyncio.Queue(maxsize=32)
        try:
            self._loop.run_until_complete(self._main_loop())
        except (asyncio.CancelledError, RuntimeError):
            pass
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending and not self._loop.is_closed():
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                if not self._loop.is_closed():
                    self._loop.close()
            except Exception:
                pass

    async def _main_loop(self):
        backoff = 0.5
        while self._running:
            if not self.socket_path.exists():
                await asyncio.sleep(backoff)
                backoff = min(2.0, backoff * 1.5)
                continue

            try:
                logger.info("WS", f"Connecting to daemon at {self.socket_path}...")
                async with websockets.unix_connect(str(self.socket_path), "ws://localhost/ws") as ws:
                    self._ws = ws
                    backoff = 0.5
                    logger.success("WS", "Connected to daemon event stream.")
                    await ws.send(json.dumps({"type": "register", "role": "ui"}))

                    reader_task = asyncio.create_task(self._read_events(ws))
                    writer_task = asyncio.create_task(self._write_events(ws))
                    heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

                    done, pending = await asyncio.wait(
                        [reader_task, writer_task, heartbeat_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()

            except Exception as e:
                logger.warn("WS", f"WebSocket connection error: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(2.0, backoff * 1.5)
            finally:
                self._ws = None

    async def _read_events(self, ws):
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    msg_type = data.get("type")
                    if msg_type == "state":
                        state_name = data.get("state", "idle")
                        self.on_state(state_name)
                    elif msg_type == "level":
                        level_val = float(data.get("value", 0.0))
                        self.on_level(level_val)
                    elif msg_type == "tts_chunk" and self.on_tts_chunk:
                        sentence = data.get("sentence", "")
                        is_final = bool(data.get("is_final", False))
                        self.on_tts_chunk(sentence, is_final)
                    elif msg_type == "exit" and self.on_exit:
                        goodbye_msg = data.get("goodbye_msg", "Goodbye!")
                        self.on_exit(goodbye_msg)
                except Exception as e:
                    logger.warn("WS", f"Failed to parse incoming WS message: {e}")
        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass

    async def _write_events(self, ws):
        while self._running:
            item = await self._outgoing_queue.get()
            try:
                if isinstance(item, (int, float)):
                    msg = json.dumps({"type": "level", "value": item})
                elif isinstance(item, dict):
                    msg = json.dumps(item)
                else:
                    continue
                await ws.send(msg)
            except Exception:
                break

    async def _heartbeat_loop(self, ws):
        """Sends periodic heartbeat to daemon every 1.5 seconds."""
        while self._running:
            try:
                await asyncio.sleep(1.5)
                msg = json.dumps({"type": "heartbeat", "component": "ui", "status": "up"})
                await ws.send(msg)
            except Exception:
                break

    def send_message(self, msg: dict):
        """Thread-safe generic message push."""
        if not self._running or self._loop is None or self._outgoing_queue is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._outgoing_queue.put_nowait, msg)
        except Exception:
            pass

    def send_heartbeat(self, component: str = "ui", **kwargs):
        """Sends component heartbeat with optional metadata."""
        payload = {"type": "heartbeat", "component": component, "status": "up"}
        payload.update(kwargs)
        self.send_message(payload)

    def send_event(self, event: str, component: str, message: str, payload: Optional[dict] = None):
        """Emits a component event to the daemon's central telemetry logger."""
        self.send_message({
            "type": "component_event",
            "event": event,
            "component": component,
            "message": message,
            "payload": payload or {},
        })

    def send_playback_finished(self):
        """Thread-safe notification to the daemon that TTS playback finished."""
        if not self._running or self._loop is None or self._outgoing_queue is None:
            return
        try:
            self._loop.call_soon_threadsafe(
                self._outgoing_queue.put_nowait,
                {"type": "tts_playback_finished"}
            )
        except Exception:
            pass

    def send_level(self, level: float):
        """Thread-safe level push to the daemon. Discards stale levels if queue is full."""
        if not self._running or self._loop is None or self._outgoing_queue is None:
            return
        level = max(0.0, min(1.0, float(level)))
        try:
            # Drop older level if one is already pending
            if self._outgoing_queue.full():
                try:
                    self._outgoing_queue.get_nowait()
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._outgoing_queue.put_nowait, level)
        except Exception:
            pass

    def stop(self):
        self._running = False
        if self._loop and self._loop.is_running():
            def _cleanup():
                for task in asyncio.all_tasks(self._loop):
                    task.cancel()
                self._loop.stop()
            self._loop.call_soon_threadsafe(_cleanup)

