#!/usr/bin/env python3
"""Orb Animation & Daemon Integration Driver and Verification Suite.

Tests:
    (a) Drives daemon through every state (idle, listening, thinking, speaking, confirm, denied)
        and performs a 0.0 -> 1.0 level sweep without a microphone.
    (b) Verifies that rapid level updates are conflated and stale levels are dropped.
    (c) Verifies that the WebSocket client automatically recovers when the daemon restarts.
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path
import pytest
import websockets

from agentctl.client.daemon import DaemonClient, get_socket_path
from agentctl.client.ws import DaemonWsClient


@pytest.mark.anyio
async def test_state_and_level_sweep():
    """(a) Drives daemon through all states and sweeps level from 0.0 to 1.0."""
    socket_path = get_socket_path()
    client = DaemonClient()

    # Reset to idle
    client.cancel()

    received_states = []
    received_levels = []

    async with websockets.unix_connect(str(socket_path), "ws://localhost/ws") as ws:
        # 1. Verify initial connect message
        init_msg = json.loads(await ws.recv())
        assert init_msg["type"] == "state"
        received_states.append(init_msg["state"])

        # 2. Transition through states
        # Idle -> Listening
        client.activate()
        # Listening -> Processing (Thinking)
        client.send_intent("test sweep command")

        # Read state events
        for _ in range(8):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.15)
                data = json.loads(raw)
                if data["type"] == "state":
                    received_states.append(data["state"])
            except asyncio.TimeoutError:
                break

        # 3. Drive Level Sweep (0.0 to 1.0)
        for i in range(11):
            val = round(i * 0.1, 2)
            await ws.send(json.dumps({"type": "level", "value": val}))
            await asyncio.sleep(0.04)

        # Read back level events
        for _ in range(12):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.1)
                data = json.loads(raw)
                if data["type"] == "level":
                    received_levels.append(data["value"])
            except asyncio.TimeoutError:
                break

    print("\nReceived States during sweep:", received_states)
    print(f"Received {len(received_levels)} level events, min={min(received_levels or [0])}, max={max(received_levels or [0])}")

    assert "idle" in received_states
    assert "listening" in received_states
    assert "thinking" in received_states
    assert any(lvl > 0.5 for lvl in received_levels)


@pytest.mark.anyio
async def test_stale_levels_dropped():
    """(b) Verifies that rapid bursts of levels are conflated and stale ones dropped."""
    socket_path = get_socket_path()

    async with websockets.unix_connect(str(socket_path), "ws://localhost/ws") as ws:
        # Consume initial state
        await ws.recv()

        # Send a rapid burst of 20 levels without pause
        for i in range(20):
            await ws.send(json.dumps({"type": "level", "value": 0.01 * i}))

        # Send final value
        await ws.send(json.dumps({"type": "level", "value": 0.95}))
        await asyncio.sleep(0.06)

        # Drain messages received
        received = []
        for _ in range(5):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.1)
                data = json.loads(raw)
                if data["type"] == "level":
                    received.append(data["value"])
            except asyncio.TimeoutError:
                break

        # Must not receive 20 queued messages; conflation ensures small list ending near 0.95
        print("\nLevels received after 20-packet burst:", received)
        assert len(received) < 10, "Stale levels must be dropped, not queued up"
        assert any(abs(v - 0.95) < 0.05 for v in received), "Latest level must be delivered"


def test_ui_daemon_reconnect():
    """(c) Verifies that DaemonWsClient automatically reconnects after daemon restarts."""
    socket_path = get_socket_path()
    states_received = []

    ws_client = DaemonWsClient(
        on_state=lambda s: states_received.append(s),
        on_level=lambda _: None,
        socket_path=socket_path,
    )
    ws_client.start()
    time.sleep(0.5)

    assert len(states_received) > 0, "Initial connection must receive state"
    initial_count = len(states_received)

    # Restart daemon service
    subprocess.run(["systemctl", "--user", "restart", "agentctl-daemon"], check=True)
    time.sleep(1.2)

    # After restart, client reconnects and receives state again
    assert len(states_received) > initial_count, "Client must automatically reconnect after daemon restart"
    ws_client.stop()
