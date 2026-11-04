import os
from pathlib import Path
from typing import Any, Dict, Optional
import httpx


def get_socket_path() -> Path:
    """Returns the Unix Domain Socket path for the daemon."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "agentctl" / "daemon.sock"
    uid = os.getuid()
    return Path(f"/tmp/agentctl-{uid}/daemon.sock")


class DaemonClient:
    """Client for communicating with the agentctl-daemon over Unix Domain Socket."""

    def __init__(self, socket_path: Optional[Path] = None, timeout: float = 2.0):
        self.socket_path = str(socket_path or get_socket_path())
        self.timeout = timeout
        transport = httpx.HTTPTransport(uds=self.socket_path)
        self.client = httpx.Client(transport=transport, base_url="http://agentctl", timeout=timeout)

    def is_alive(self) -> bool:
        try:
            resp = self.client.get("/status")
            return resp.status_code == 200
        except Exception:
            return False

    def activate(self) -> Dict[str, Any]:
        resp = self.client.post("/activate", json={})
        resp.raise_for_status()
        return resp.json()

    def cancel(self) -> Dict[str, Any]:
        resp = self.client.post("/cancel", json={})
        resp.raise_for_status()
        return resp.json()

    def send_intent(self, text: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        resp = self.client.post("/intent", json={"text": text, "session_id": session_id})
        resp.raise_for_status()
        return resp.json()

    def get_status(self) -> Dict[str, Any]:
        resp = self.client.get("/status")
        resp.raise_for_status()
        return resp.json()

    def approve(self, task_id: str, action_hash: str) -> Dict[str, Any]:
        resp = self.client.post("/approve", json={"task_id": task_id, "hash": action_hash})
        resp.raise_for_status()
        return resp.json()

    def deny(self, task_id: str, action_hash: str) -> Dict[str, Any]:
        resp = self.client.post("/deny", json={"task_id": task_id, "hash": action_hash})
        resp.raise_for_status()
        return resp.json()

    def kill(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        resp = self.client.post("/kill", json={"task_id": task_id} if task_id else {})
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.client.close()

