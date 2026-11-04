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
        socket_path: Optional[Path] = None,
    ):
        super().__init__(daemon=True)
        self.on_state = on_state
        self.on_level = on_level
        self.socket_path = socket_path or SOCKET_PATH
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws: Optional[websockets.ClientConnection] = None
        self._outgoing_queue: Optional[asyncio.Queue] = None

    def run(self):
        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._outgoing_queue = asyncio.Queue(maxsize=1)
        self._loop.run_until_complete(self._main_loop())

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

                    reader_task = asyncio.create_task(self._read_events(ws))
                    writer_task = asyncio.create_task(self._write_events(ws))

                    done, pending = await asyncio.wait(
                        [reader_task, writer_task],
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
            except Exception as e:
                logger.warn("WS", f"Failed to parse incoming WS message: {e}")

    async def _write_events(self, ws):
        while self._running:
            val = await self._outgoing_queue.get()
            try:
                msg = json.dumps({"type": "level", "value": val})
                await ws.send(msg)
            except Exception:
                break

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
            self._loop.call_soon_threadsafe(self._loop.stop)
